#!/usr/bin/env python3
"""Upsert Threads secrets into Infisical from a JSON payload.

Expected payload shape:
{
  "THREADS_APP_ID": "...",
  "THREADS_APP_SECRET": "...",
  "THREADS_USER_ID": "...",
  "THREADS_ACCESS_TOKEN": "...",
  "THREADS_TOKEN_EXPIRES_AT": "..."
}
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))


def _resolve_setting(cli_value: str, env_key: str, default: str = "") -> str:
    raw = str(cli_value or "").strip()
    if raw:
        return raw
    return str(os.getenv(env_key) or default).strip()


def _load_updates(*, raw_json: str, json_file: str, env_var: str) -> dict[str, str]:
    payload_raw = str(raw_json or "").strip()
    if not payload_raw and json_file:
        payload_raw = Path(json_file).expanduser().read_text(encoding="utf-8").strip()
    if not payload_raw:
        payload_raw = str(os.getenv(env_var) or "").strip()
    if not payload_raw:
        raise ValueError(
            "No update payload found. Provide --updates-json, --updates-file, "
            f"or set {env_var}."
        )

    try:
        parsed = json.loads(payload_raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON payload: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Update payload must be a JSON object")

    updates: dict[str, str] = {}
    for key, value in parsed.items():
        name = str(key or "").strip()
        if not name:
            continue
        if value is None:
            continue
        updates[name] = str(value)
    if not updates:
        raise ValueError("Update payload is empty after normalization")
    return updates


def _build_client(*, client_id: str, client_secret: str, api_url: str):
    from infisical_client import (
        AuthenticationOptions,
        ClientSettings,
        InfisicalClient,
        UniversalAuthMethod,
    )

    settings = ClientSettings(
        auth=AuthenticationOptions(
            universal_auth=UniversalAuthMethod(
                client_id=client_id,
                client_secret=client_secret,
            )
        ),
        site_url=api_url or None,
    )
    return InfisicalClient(settings)


def _list_existing_keys(*, client, environment: str, project_id: str, secret_path: str) -> set[str]:
    from infisical_client import ListSecretsOptions

    existing = client.listSecrets(
        options=ListSecretsOptions(
            environment=environment,
            project_id=project_id,
            path=secret_path,
            recursive=False,
            include_imports=False,
        )
    )
    keys: set[str] = set()
    for item in existing:
        key = str(getattr(item, "secret_key", "") or "").strip()
        if key:
            keys.add(key)
    return keys


def _sync_updates(
    *,
    client,
    updates: dict[str, str],
    existing_keys: set[str],
    environment: str,
    project_id: str,
    secret_path: str,
    dry_run: bool,
) -> tuple[int, int]:
    from infisical_client import CreateSecretOptions, UpdateSecretOptions

    created = 0
    updated = 0
    for name, value in updates.items():
        if dry_run:
            action = "update" if name in existing_keys else "create"
            print(f"DRY_RUN {action.upper()} {name}")
            if action == "create":
                created += 1
            else:
                updated += 1
            continue

        if name in existing_keys:
            client.updateSecret(
                options=UpdateSecretOptions(
                    environment=environment,
                    project_id=project_id,
                    path=secret_path,
                    secret_name=name,
                    secret_value=value,
                )
            )
            print(f"UPDATED {name}")
            updated += 1
            continue

        client.createSecret(
            options=CreateSecretOptions(
                environment=environment,
                project_id=project_id,
                path=secret_path,
                secret_name=name,
                secret_value=value,
            )
        )
        print(f"CREATED {name}")
        created += 1
    return created, updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--updates-json", default="", help="Raw JSON object of key/value secret updates")
    parser.add_argument("--updates-file", default="", help="Path to JSON file with key/value secret updates")
    parser.add_argument(
        "--updates-env-var",
        default="INFISICAL_SECRET_UPDATES_JSON",
        help="Env var name used when --updates-json/--updates-file are not provided",
    )
    parser.add_argument("--project-id", default="")
    parser.add_argument("--environment", default="")
    parser.add_argument("--secret-path", default="")
    parser.add_argument("--client-id", default="")
    parser.add_argument("--client-secret", default="")
    parser.add_argument("--api-url", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project_id = _resolve_setting(args.project_id, "INFISICAL_PROJECT_ID")
    environment = _resolve_setting(args.environment, "INFISICAL_ENVIRONMENT", "dev") or "dev"
    secret_path = _resolve_setting(args.secret_path, "INFISICAL_SECRET_PATH", "/") or "/"
    client_id = _resolve_setting(args.client_id, "INFISICAL_CLIENT_ID")
    client_secret = _resolve_setting(args.client_secret, "INFISICAL_CLIENT_SECRET")
    api_url = _resolve_setting(args.api_url, "INFISICAL_API_URL", "https://app.infisical.com").rstrip("/")

    missing: list[str] = []
    if not project_id:
        missing.append("INFISICAL_PROJECT_ID")
    if not client_id:
        missing.append("INFISICAL_CLIENT_ID")
    if not client_secret:
        missing.append("INFISICAL_CLIENT_SECRET")
    if missing:
        print("ERROR=Missing required Infisical settings: " + ", ".join(missing))
        return 2

    try:
        updates = _load_updates(
            raw_json=args.updates_json,
            json_file=args.updates_file,
            env_var=str(args.updates_env_var),
        )
    except Exception as exc:
        print(f"ERROR=invalid_update_payload:{type(exc).__name__}:{exc}")
        return 2

    try:
        client = _build_client(client_id=client_id, client_secret=client_secret, api_url=api_url)
        existing_keys = _list_existing_keys(
            client=client,
            environment=environment,
            project_id=project_id,
            secret_path=secret_path,
        )
        created, updated = _sync_updates(
            client=client,
            updates=updates,
            existing_keys=existing_keys,
            environment=environment,
            project_id=project_id,
            secret_path=secret_path,
            dry_run=bool(args.dry_run),
        )
    except Exception as exc:
        print(f"ERROR=infisical_sync_failed:{type(exc).__name__}:{exc}")
        return 1

    print(f"INFISICAL_PROJECT_ID={project_id}")
    print(f"INFISICAL_ENVIRONMENT={environment}")
    print(f"INFISICAL_SECRET_PATH={secret_path}")
    print(f"SYNC_CREATED={created}")
    print(f"SYNC_UPDATED={updated}")
    print(f"SYNC_TOTAL={created + updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
