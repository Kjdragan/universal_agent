#!/usr/bin/env python3
"""Create an Infisical environment if needed and upsert secrets into it."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import httpx

API_URL_DEFAULT = "https://app.infisical.com"


def _load_dotenv_into_environ() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    dotenv_path = repo_root / ".env"
    if not dotenv_path.exists():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("'\"")


def _require_env(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _authenticate(api_url: str, client_id: str, client_secret: str) -> str:
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


def _list_environments(api_url: str, token: str, project_id: str) -> list[dict]:
    resp = httpx.get(
        f"{api_url}/api/v1/workspace/{project_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20.0,
    )
    resp.raise_for_status()
    data = resp.json()
    envs = data.get("workspace", {}).get("environments") or data.get("environments") or []
    return envs if isinstance(envs, list) else []


def _create_environment(api_url: str, token: str, project_id: str, name: str, slug: str) -> None:
    resp = httpx.post(
        f"{api_url}/api/v1/projects/{project_id}/environments",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"name": name, "slug": slug, "position": 10},
        timeout=20.0,
    )
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            payload = resp.json()
            detail = str(payload.get("message") or payload.get("error") or "").strip()
        except Exception:
            detail = resp.text.strip()
        if detail:
            raise RuntimeError(f"Failed to create Infisical environment {slug!r}: {detail}") from exc
        raise


def _fetch_secrets(api_url: str, token: str, project_id: str, environment: str, secret_path: str) -> dict[str, str]:
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
    if resp.status_code == 404:
        return {}
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


def _batch_write(
    api_url: str,
    token: str,
    project_id: str,
    environment: str,
    secret_path: str,
    *,
    method: str,
    secrets: dict[str, str],
) -> None:
    if not secrets:
        return
    resp = httpx.request(
        method,
        f"{api_url}/api/v4/secrets/batch",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "projectId": project_id,
            "environment": environment,
            "secretPath": secret_path,
            "secrets": [{"secretKey": key, "secretValue": value} for key, value in sorted(secrets.items())],
        },
        timeout=30.0,
    )
    resp.raise_for_status()


def _parse_secret_items(items: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise RuntimeError(f"Invalid --secret entry {item!r}; expected KEY=VALUE")
        parsed[key.strip()] = value
    return parsed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upsert secrets into an Infisical environment/path.")
    parser.add_argument("--environment", required=True, help="Target Infisical environment slug.")
    parser.add_argument("--environment-name", default="", help="Display name to use if the environment must be created.")
    parser.add_argument("--secret-path", default="/", help="Target secret path (default: /)")
    parser.add_argument("--ensure-environment", action="store_true", help="Create the environment if it does not exist.")
    parser.add_argument("--secret", action="append", default=[], help="Literal KEY=VALUE secret to upsert.")
    parser.add_argument("--secret-env", action="append", default=[], help="Environment variable name to upsert using its current value.")
    parser.add_argument("--dry-run", action="store_true", help="Show intended operations without writing.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _load_dotenv_into_environ()

    client_id = _require_env("INFISICAL_CLIENT_ID")
    client_secret = _require_env("INFISICAL_CLIENT_SECRET")
    project_id = _require_env("INFISICAL_PROJECT_ID")
    api_url = (os.getenv("INFISICAL_API_URL") or API_URL_DEFAULT).strip().rstrip("/")

    secrets = _parse_secret_items(args.secret)
    for env_name in args.secret_env:
        key = str(env_name or "").strip()
        if not key:
            continue
        secrets[key] = _require_env(key)
    if not secrets:
        raise RuntimeError("No secrets selected. Use --secret and/or --secret-env.")

    token = _authenticate(api_url, client_id, client_secret)
    existing_envs = _list_environments(api_url, token, project_id)
    env_slugs = {str(item.get("slug") or "").strip() for item in existing_envs if isinstance(item, dict)}
    if args.environment not in env_slugs:
        if not args.ensure_environment:
            raise RuntimeError(
                f"Environment {args.environment!r} does not exist. Re-run with --ensure-environment to create it."
            )
        env_name = str(args.environment_name or args.environment).strip()
        if args.dry_run:
            print(f"INFISICAL_ENV_CREATE environment={args.environment} name={env_name}")
        else:
            _create_environment(api_url, token, project_id, env_name, args.environment)
            print(f"INFISICAL_ENV_CREATED environment={args.environment}")

    target = _fetch_secrets(api_url, token, project_id, args.environment, args.secret_path)
    to_create = {k: v for k, v in secrets.items() if k not in target}
    to_update = {k: v for k, v in secrets.items() if k in target and target[k] != v}
    unchanged = sorted(k for k, v in secrets.items() if k in target and target[k] == v)

    print(
        "INFISICAL_UPSERT_PLAN "
        f"environment={args.environment} path={args.secret_path} "
        f"create={len(to_create)} update={len(to_update)} unchanged={len(unchanged)}"
    )
    if unchanged:
        print(f"INFISICAL_UNCHANGED_KEYS={','.join(unchanged)}")
    if args.dry_run:
        if to_create:
            print(f"INFISICAL_WOULD_CREATE_KEYS={','.join(sorted(to_create))}")
        if to_update:
            print(f"INFISICAL_WOULD_UPDATE_KEYS={','.join(sorted(to_update))}")
        return 0

    _batch_write(
        api_url,
        token,
        project_id,
        args.environment,
        args.secret_path,
        method="POST",
        secrets=to_create,
    )
    _batch_write(
        api_url,
        token,
        project_id,
        args.environment,
        args.secret_path,
        method="PATCH",
        secrets=to_update,
    )
    print(f"INFISICAL_UPSERT_OK environment={args.environment} path={args.secret_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
