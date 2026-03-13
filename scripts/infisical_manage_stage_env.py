#!/usr/bin/env python3
"""Admin helper for stage-based Infisical environments.

This tool is intended for controlled operator use, not routine deploy-time
provisioning. It works with the canonical stage environments:
`development`, `staging`, and `production`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Iterable

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from infisical_provision_factory_env import (
    _authenticate,
    _bulk_create_secrets,
    _bulk_update_secrets,
    _list_environments,
    _fetch_secrets,
    _get_infisical_creds,
    _load_dotenv_into_environ,
)

logger = logging.getLogger(__name__)

_VALID_STAGES = {"development", "staging", "production"}
_LEGACY_STAGE_ALIASES = {
    "dev": "development",
    "prod": "production",
    "kevins-desktop": "staging",
}


def _validate_stage(stage: str) -> str:
    normalized = str(stage or "").strip().lower()
    normalized = _LEGACY_STAGE_ALIASES.get(normalized, normalized)
    if normalized not in _VALID_STAGES:
        raise ValueError(f"Unsupported stage '{stage}'. Expected one of: {', '.join(sorted(_VALID_STAGES))}")
    return normalized


def _load_env_index() -> tuple[str, str, str, str, str, dict[str, dict[str, str]]]:
    client_id, client_secret, project_id, api_url = _get_infisical_creds()
    token = _authenticate(api_url, client_id, client_secret)
    environments = _list_environments(api_url, token, project_id)
    by_slug = {str(item["slug"]): item for item in environments}
    return client_id, client_secret, project_id, api_url, token, by_slug


def _load_stage_secrets(stage: str) -> tuple[str, dict[str, str]]:
    client_id, client_secret, project_id, api_url = _get_infisical_creds()
    token = _authenticate(api_url, client_id, client_secret)
    secrets = _fetch_secrets(api_url, token, project_id, _validate_stage(stage))
    return api_url, secrets


def _parse_preserve(values: Iterable[str]) -> set[str]:
    keep: set[str] = set()
    for raw in values:
        for item in str(raw or "").split(","):
            text = item.strip()
            if text:
                keep.add(text)
    return keep


def _bulk_delete_secrets(
    *,
    api_url: str,
    token: str,
    project_id: str,
    environment: str,
    secret_path: str,
    secret_keys: Iterable[str],
) -> None:
    secrets = [{"secretKey": key, "type": "shared"} for key in sorted({str(k).strip() for k in secret_keys if str(k).strip()})]
    if not secrets:
        return
    response = httpx.request(
        "DELETE",
        f"{api_url}/api/v3/secrets/batch/raw",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "workspaceId": project_id,
            "environment": environment,
            "secretPath": secret_path,
            "secrets": secrets,
        },
        timeout=30.0,
    )
    response.raise_for_status()


def cmd_backup(args: argparse.Namespace) -> int:
    _, secrets = _load_stage_secrets(args.environment)
    payload = {
        "environment": _validate_stage(args.environment),
        "secret_count": len(secrets),
        "secrets": secrets,
    }
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote backup for {payload['environment']} to {output_path}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    _, secrets = _load_stage_secrets(args.environment)
    required = [item.strip() for item in (args.require or []) if str(item or "").strip()]
    missing = [key for key in required if not str(secrets.get(key) or "").strip()]
    summary = {
        "environment": _validate_stage(args.environment),
        "secret_count": len(secrets),
        "required": required,
        "missing": missing,
        "ok": not missing,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if not missing else 1


def cmd_compare(args: argparse.Namespace) -> int:
    _, left = _load_stage_secrets(args.left)
    _, right = _load_stage_secrets(args.right)
    left_only = sorted(set(left) - set(right))
    right_only = sorted(set(right) - set(left))
    changed = sorted(key for key in (set(left) & set(right)) if left.get(key) != right.get(key))
    print(
        json.dumps(
            {
                "left": _validate_stage(args.left),
                "right": _validate_stage(args.right),
                "left_only": left_only,
                "right_only": right_only,
                "changed": changed,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    source_stage = _validate_stage(args.source)
    target_stage = _validate_stage(args.target)
    if source_stage == target_stage:
        raise SystemExit("Source and target stages must differ")

    client_id, client_secret, project_id, api_url = _get_infisical_creds()
    token = _authenticate(api_url, client_id, client_secret)
    source = _fetch_secrets(api_url, token, project_id, source_stage)
    target = _fetch_secrets(api_url, token, project_id, target_stage)

    preserve = _parse_preserve(args.preserve or [])
    merged = dict(source)
    for key in preserve:
        if key in target:
            merged[key] = target[key]
    extras_to_delete = sorted(key for key in target if key not in merged and key not in preserve)

    to_create = {key: value for key, value in merged.items() if key not in target}
    to_update = {key: value for key, value in merged.items() if key in target and target[key] != value}

    preview = {
        "source": source_stage,
        "target": target_stage,
        "source_secret_count": len(source),
        "target_secret_count": len(target),
        "preserve": sorted(preserve),
        "upsert_secret_count": len(merged),
        "create_secret_count": len(to_create),
        "update_secret_count": len(to_update),
        "delete_secret_count": len(extras_to_delete) if args.prune_extras else 0,
        "extras_to_delete": extras_to_delete if args.prune_extras else [],
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps(preview, indent=2, sort_keys=True))
    if args.dry_run:
        return 0

    if to_create:
        _bulk_create_secrets(api_url, token, project_id, target_stage, to_create)
    if to_update:
        _bulk_update_secrets(api_url, token, project_id, target_stage, to_update)
    if args.prune_extras:
        _bulk_delete_secrets(
            api_url=api_url,
            token=token,
            project_id=project_id,
            environment=target_stage,
            secret_path="/",
            secret_keys=extras_to_delete,
        )
    print(f"Synchronized {len(merged)} secrets from {source_stage} to {target_stage}")
    return 0


def cmd_rename(args: argparse.Namespace) -> int:
    source_slug = str(args.from_env or "").strip().lower()
    target_slug = _validate_stage(args.to_env)
    target_name = str(args.name or "").strip()
    if not source_slug:
        raise SystemExit("--from-env is required")
    if not target_name:
        raise SystemExit("--name is required")

    _, _, project_id, api_url, token, by_slug = _load_env_index()
    if source_slug not in by_slug:
        raise SystemExit(f"Environment {source_slug!r} not found")

    source_env = by_slug[source_slug]
    env_id = str(source_env["id"])
    payload = {"slug": target_slug, "name": target_name}
    response = httpx.patch(
        f"{api_url}/api/v1/workspace/{project_id}/environments/{env_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=20.0,
    )
    response.raise_for_status()
    body = response.json()
    print(
        json.dumps(
            {
                "ok": True,
                "from_slug": source_slug,
                "to_slug": target_slug,
                "name": target_name,
                "environment": body.get("environment", {}),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage stage-based Infisical environments.")
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup", help="Export one stage environment to JSON.")
    backup.add_argument("--environment", required=True, help="Stage environment to back up.")
    backup.add_argument("--output", required=True, help="Path to write the JSON backup.")
    backup.set_defaults(func=cmd_backup)

    verify = sub.add_parser("verify", help="Verify required keys exist in a stage environment.")
    verify.add_argument("--environment", required=True, help="Stage environment to inspect.")
    verify.add_argument("--require", action="append", default=[], help="Required key. May be repeated.")
    verify.set_defaults(func=cmd_verify)

    compare = sub.add_parser("compare", help="Compare two stage environments.")
    compare.add_argument("--left", required=True, help="Left stage environment.")
    compare.add_argument("--right", required=True, help="Right stage environment.")
    compare.set_defaults(func=cmd_compare)

    sync = sub.add_parser("sync", help="Copy source stage secrets into target stage.")
    sync.add_argument("--source", required=True, help="Source stage environment.")
    sync.add_argument("--target", required=True, help="Target stage environment.")
    sync.add_argument(
        "--preserve",
        action="append",
        default=[],
        help="Comma-separated or repeated keys to preserve from the target stage.",
    )
    sync.add_argument(
        "--prune-extras",
        action="store_true",
        help="Delete target-only keys that are not in source or preserve list.",
    )
    sync.add_argument("--dry-run", action="store_true", help="Preview the sync without writing.")
    sync.set_defaults(func=cmd_sync)

    rename = sub.add_parser("rename", help="Rename a live Infisical environment slug/name.")
    rename.add_argument("--from-env", required=True, help="Current environment slug.")
    rename.add_argument("--to-env", required=True, help="New canonical stage slug.")
    rename.add_argument("--name", required=True, help="Display name for the environment.")
    rename.set_defaults(func=cmd_rename)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _load_dotenv_into_environ()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
