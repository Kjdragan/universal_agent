#!/usr/bin/env python3
"""Sync selected Infisical secrets from one environment to another.

Use this for controlled cross-environment sync (for example, shared Z AI /
Anthropic-compatible LLM credentials) without cloning every key.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
from typing import Any

API_URL_DEFAULT = "https://app.infisical.com"

PRESET_ZAI_LLM_KEYS = [
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ZAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "MODEL_NAME",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "LLM_PROVIDER_OVERRIDE",
    "Z_AI_MODE",
    "UA_CLAUDE_CODE_MODEL",
]


def _load_dotenv_into_environ() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    dotenv_path = repo_root / ".env"
    if not dotenv_path.exists():
        return
    try:
        from dotenv import dotenv_values

        for key, value in dotenv_values(dotenv_path).items():
            if key and value is not None and key not in os.environ:
                os.environ[key] = value
    except ImportError:
        for line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def _require_env(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _authenticate(api_url: str, client_id: str, client_secret: str) -> str:
    import httpx

    resp = httpx.post(
        f"{api_url}/api/v1/auth/universal-auth/login",
        json={"clientId": client_id, "clientSecret": client_secret},
        timeout=20.0,
    )
    resp.raise_for_status()
    token = str(resp.json().get("accessToken") or "").strip()
    if not token:
        raise RuntimeError("Infisical auth response missing accessToken")
    return token


def _fetch_secrets(
    api_url: str,
    token: str,
    project_id: str,
    environment: str,
    secret_path: str,
) -> dict[str, str]:
    import httpx

    resp = httpx.get(
        f"{api_url}/api/v3/secrets/raw",
        params={
            "workspaceId": project_id,
            "environment": environment,
            "secretPath": secret_path,
            "recursive": "true",
            "include_imports": "true",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    payload = resp.json()
    out: dict[str, str] = {}
    for item in payload.get("secrets", []):
        key = str(item.get("secretKey") or "").strip()
        if not key:
            continue
        value = item.get("secretValue")
        if value is None:
            value = item.get("secret_value")
        out[key] = str(value or "")
    return out


def _bulk_create(
    api_url: str,
    token: str,
    project_id: str,
    environment: str,
    secret_path: str,
    secrets: dict[str, str],
) -> None:
    import httpx

    if not secrets:
        return
    resp = httpx.post(
        f"{api_url}/api/v4/secrets/batch",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "projectId": project_id,
            "environment": environment,
            "secretPath": secret_path,
            "secrets": [{"secretKey": k, "secretValue": v} for k, v in sorted(secrets.items())],
        },
        timeout=30.0,
    )
    resp.raise_for_status()


def _bulk_update(
    api_url: str,
    token: str,
    project_id: str,
    environment: str,
    secret_path: str,
    secrets: dict[str, str],
) -> None:
    import httpx

    if not secrets:
        return
    resp = httpx.patch(
        f"{api_url}/api/v4/secrets/batch",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "projectId": project_id,
            "environment": environment,
            "secretPath": secret_path,
            "secrets": [{"secretKey": k, "secretValue": v} for k, v in sorted(secrets.items())],
        },
        timeout=30.0,
    )
    resp.raise_for_status()


def _select_keys(
    source_keys: set[str],
    *,
    preset_zai_llm: bool,
    include_keys: list[str],
    include_regexes: list[str],
    exclude_keys: set[str],
) -> list[str]:
    selected: set[str] = set()
    if preset_zai_llm:
        selected.update(PRESET_ZAI_LLM_KEYS)
    selected.update(k.strip() for k in include_keys if k.strip())
    for regex in include_regexes:
        pattern = re.compile(regex)
        selected.update(k for k in source_keys if pattern.search(k))
    selected.difference_update(exclude_keys)
    return sorted(selected)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync selected secrets from source Infisical environment to target environment.",
    )
    parser.add_argument("--source-env", default="dev", help="Source environment slug (default: dev)")
    parser.add_argument("--target-env", required=True, help="Target environment slug")
    parser.add_argument("--secret-path", default="/", help="Secret path (default: /)")
    parser.add_argument(
        "--preset",
        action="append",
        default=[],
        choices=["zai-llm"],
        help="Named key set to sync (supported: zai-llm)",
    )
    parser.add_argument("--key", action="append", default=[], help="Explicit key to sync (repeatable)")
    parser.add_argument(
        "--regex",
        action="append",
        default=[],
        help="Regex pattern; matching source keys are synced (repeatable)",
    )
    parser.add_argument("--exclude-key", action="append", default=[], help="Explicit key to exclude (repeatable)")
    parser.add_argument(
        "--fail-on-missing",
        action="store_true",
        help="Fail if any selected key is missing in source environment",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show plan only; do not write secrets")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _load_dotenv_into_environ()

    client_id = _require_env("INFISICAL_CLIENT_ID")
    client_secret = _require_env("INFISICAL_CLIENT_SECRET")
    project_id = _require_env("INFISICAL_PROJECT_ID")
    api_url = (os.getenv("INFISICAL_API_URL") or API_URL_DEFAULT).strip().rstrip("/")

    if args.source_env.strip() == args.target_env.strip():
        raise RuntimeError("source and target environments must be different")

    token = _authenticate(api_url, client_id, client_secret)
    source = _fetch_secrets(api_url, token, project_id, args.source_env, args.secret_path)
    target = _fetch_secrets(api_url, token, project_id, args.target_env, args.secret_path)

    selected = _select_keys(
        set(source.keys()),
        preset_zai_llm=("zai-llm" in set(args.preset)),
        include_keys=args.key,
        include_regexes=args.regex,
        exclude_keys=set(k.strip() for k in args.exclude_key if k.strip()),
    )
    if not selected:
        raise RuntimeError("No keys selected. Use --preset, --key, or --regex.")

    missing = [k for k in selected if k not in source]
    if args.fail_on_missing and missing:
        raise RuntimeError(f"Selected keys missing in source env '{args.source_env}': {', '.join(missing)}")

    transferable = {k: source[k] for k in selected if k in source}
    to_create = {k: v for k, v in transferable.items() if k not in target}
    to_update = {k: v for k, v in transferable.items() if k in target and target[k] != v}
    unchanged = sorted(k for k, v in transferable.items() if k in target and target[k] == v)

    print(f"Source env:   {args.source_env}")
    print(f"Target env:   {args.target_env}")
    print(f"Secret path:  {args.secret_path}")
    print(f"Selected:     {len(selected)}")
    print(f"Missing src:  {len(missing)}")
    print(f"Transferable: {len(transferable)}")
    print(f"Create:       {len(to_create)}")
    print(f"Update:       {len(to_update)}")
    print(f"Unchanged:    {len(unchanged)}")
    if missing:
        print("Missing keys:")
        for key in missing:
            print(f"- {key}")
    if to_create:
        print("Create keys:")
        for key in sorted(to_create.keys()):
            print(f"- {key}")
    if to_update:
        print("Update keys:")
        for key in sorted(to_update.keys()):
            print(f"- {key}")

    if args.dry_run:
        print("Dry run complete. No writes performed.")
        return 0

    _bulk_update(api_url, token, project_id, args.target_env, args.secret_path, to_update)
    _bulk_create(api_url, token, project_id, args.target_env, args.secret_path, to_create)
    print("Sync complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
