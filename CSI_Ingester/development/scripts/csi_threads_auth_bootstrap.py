#!/usr/bin/env python3
"""Bootstrap Threads credentials for CSI by exchanging/refreshing tokens.

Supports:
- generating an OAuth authorization URL (manual consent step)
- exchanging a short-lived token to long-lived
- refreshing a long-lived token
- writing updated THREADS_ACCESS_TOKEN / THREADS_TOKEN_EXPIRES_AT to env file
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import secrets
import sys
import urllib.parse

import httpx

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.adapters.threads_api import ThreadsTokenManager

DEFAULT_ENV_FILE = "/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env"
DEFAULT_SCOPES = [
    "threads_basic",
    "threads_read_replies",
    "threads_manage_mentions",
    "threads_manage_insights",
    "threads_keyword_search",
    "threads_profile_discovery",
]


@dataclass(slots=True)
class BootstrapSettings:
    app_id: str
    app_secret: str
    access_token: str
    token_expires_at: str
    user_id: str


def _parse_env(path: Path) -> tuple[list[str], dict[str, str]]:
    if not path.exists():
        return [], {}
    lines = path.read_text(encoding="utf-8").splitlines()
    parsed: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip()] = value.strip()
    return lines, parsed


def _upsert_line(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}="
    replaced = False
    out: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    return out


def _resolve_setting(cli_value: str, parsed_env: dict[str, str], key: str) -> str:
    raw = str(cli_value or "").strip()
    if raw:
        return raw
    return str(parsed_env.get(key, "") or "").strip()


def _build_authorize_url(*, app_id: str, redirect_uri: str, scopes: list[str], state: str) -> str:
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "scope": ",".join(scopes),
        "response_type": "code",
        "state": state,
    }
    return "https://threads.net/oauth/authorize?" + urllib.parse.urlencode(params)


def _settings_from_args(args: argparse.Namespace, parsed_env: dict[str, str]) -> BootstrapSettings:
    return BootstrapSettings(
        app_id=_resolve_setting(args.app_id, parsed_env, "THREADS_APP_ID"),
        app_secret=_resolve_setting(args.app_secret, parsed_env, "THREADS_APP_SECRET"),
        access_token=_resolve_setting(args.access_token, parsed_env, "THREADS_ACCESS_TOKEN"),
        token_expires_at=_resolve_setting(args.token_expires_at, parsed_env, "THREADS_TOKEN_EXPIRES_AT"),
        user_id=_resolve_setting(args.user_id, parsed_env, "THREADS_USER_ID"),
    )


def _write_settings(env_path: Path, lines: list[str], updates: dict[str, str], *, dry_run: bool) -> None:
    out_lines = list(lines)
    for key, value in updates.items():
        out_lines = _upsert_line(out_lines, key, value)

    if dry_run:
        print("DRY_RUN=1")
        for key, value in updates.items():
            redacted = value
            if key in {"THREADS_ACCESS_TOKEN", "THREADS_APP_SECRET"} and value:
                redacted = value[:6] + "..." + value[-4:]
            print(f"UPDATE {key}={redacted}")
        return

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"ENV_UPDATED={env_path}")


def _serialize_updates_payload(updates: dict[str, str]) -> str:
    return json.dumps(updates, ensure_ascii=True, separators=(",", ":"))


def _emit_infisical_payload(updates: dict[str, str]) -> None:
    print("INFISICAL_SECRET_UPDATES_JSON=" + _serialize_updates_payload(updates))


def _write_infisical_json_file(path: Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize_updates_payload(updates) + "\n", encoding="utf-8")
    print(f"INFISICAL_JSON_FILE={path}")


async def _resolve_user_profile(*, access_token: str, timeout_seconds: int) -> dict[str, str]:
    if not access_token:
        return {}
    url = "https://graph.threads.net/v1.0/me"
    params = {"fields": "id,username", "access_token": access_token}
    async with httpx.AsyncClient(timeout=max(5, int(timeout_seconds))) as client:
        resp = await client.get(url, params=params)
    if resp.status_code >= 400:
        return {}
    payload = resp.json() if resp.content else {}
    if not isinstance(payload, dict):
        return {}
    out: dict[str, str] = {}
    user_id = str(payload.get("id") or "").strip()
    username = str(payload.get("username") or "").strip()
    if user_id:
        out["id"] = user_id
    if username:
        out["username"] = username
    return out


async def _exchange_auth_code_for_short_lived_token(
    *,
    app_id: str,
    app_secret: str,
    auth_code: str,
    redirect_uri: str,
    token_url: str,
    timeout_seconds: int,
) -> str:
    if not app_id:
        raise ValueError("THREADS_APP_ID is required for auth-code exchange")
    if not app_secret:
        raise ValueError("THREADS_APP_SECRET is required for auth-code exchange")
    if not auth_code:
        raise ValueError("auth_code is required for auth-code exchange")
    if not redirect_uri:
        raise ValueError("redirect_uri is required for auth-code exchange")

    url = token_url.rstrip("/") + "/oauth/access_token"
    data = {
        "client_id": app_id,
        "client_secret": app_secret,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code": auth_code,
    }
    async with httpx.AsyncClient(timeout=max(5, int(timeout_seconds))) as client:
        resp = await client.post(url, data=data)
    resp.raise_for_status()
    payload = resp.json() if resp.content else {}
    if not isinstance(payload, dict):
        raise RuntimeError("invalid auth-code exchange response")
    short_lived = str(payload.get("access_token") or "").strip()
    if not short_lived:
        raise RuntimeError("auth-code exchange did not return access_token")
    return short_lived


async def _exchange_or_refresh(args: argparse.Namespace, *, env_path: Path, lines: list[str], parsed_env: dict[str, str]) -> int:
    settings = _settings_from_args(args, parsed_env)
    if not settings.app_secret:
        print("ERROR=THREADS_APP_SECRET is required for exchange/refresh")
        return 2

    manager = ThreadsTokenManager(
        app_id=settings.app_id,
        app_secret=settings.app_secret,
        access_token=settings.access_token,
        token_expires_at=settings.token_expires_at,
        token_url=args.token_url,
        refresh_buffer_seconds=max(60, int(args.refresh_buffer_seconds)),
    )

    mode = str(args.mode or "auto").strip().lower()
    if mode not in {"auto", "exchange", "refresh"}:
        print(f"ERROR=invalid mode: {mode}")
        return 2

    short_lived_token = str(args.short_lived_token or "").strip()
    auth_code = str(args.auth_code or "").strip()
    redirect_uri = str(args.redirect_uri or "").strip()
    if not short_lived_token and auth_code:
        try:
            short_lived_token = await _exchange_auth_code_for_short_lived_token(
                app_id=settings.app_id,
                app_secret=settings.app_secret,
                auth_code=auth_code,
                redirect_uri=redirect_uri,
                token_url=args.token_url,
                timeout_seconds=max(5, int(args.timeout_seconds)),
            )
        except Exception as exc:
            print(f"ERROR=threads_auth_code_exchange_failed:{type(exc).__name__}:{exc}")
            return 1

    action = ""
    try:
        if mode == "exchange" or (mode == "auto" and short_lived_token):
            if not short_lived_token:
                print("ERROR=short-lived token required for exchange mode")
                return 2
            token, expires_at = await manager.exchange_short_lived_token(
                short_lived_token,
                timeout_seconds=max(5, int(args.timeout_seconds)),
            )
            action = "exchange"
        else:
            if not manager.access_token:
                print("ERROR=THREADS_ACCESS_TOKEN required for refresh mode")
                return 2
            token, expires_at = await manager.refresh_long_lived_token(
                timeout_seconds=max(5, int(args.timeout_seconds)),
            )
            action = "refresh"
    except Exception as exc:
        print(f"ERROR=threads_token_{mode}_failed:{type(exc).__name__}:{exc}")
        return 1

    resolved_user_profile: dict[str, str] = {}
    if bool(args.resolve_user_id) and not settings.user_id:
        try:
            resolved_user_profile = await _resolve_user_profile(
                access_token=token,
                timeout_seconds=max(5, int(args.timeout_seconds)),
            )
        except Exception:
            resolved_user_profile = {}

    effective_user_id = settings.user_id or str(resolved_user_profile.get("id") or "")
    updates = {
        "THREADS_APP_ID": settings.app_id,
        "THREADS_APP_SECRET": settings.app_secret,
        "THREADS_USER_ID": effective_user_id,
        "THREADS_ACCESS_TOKEN": token,
        "THREADS_TOKEN_EXPIRES_AT": expires_at,
    }
    if bool(args.print_infisical_json):
        _emit_infisical_payload(updates)
    infisical_json_file_raw = str(args.infisical_json_file or "").strip()
    if infisical_json_file_raw:
        _write_infisical_json_file(Path(infisical_json_file_raw).expanduser(), updates)

    if bool(args.skip_env_write):
        print("ENV_WRITE_SKIPPED=1")
    else:
        _write_settings(env_path, lines, updates, dry_run=bool(args.dry_run))

    masked = token[:6] + "..." + token[-4:] if token else ""
    print(f"THREADS_TOKEN_ACTION={action}")
    print(f"THREADS_ACCESS_TOKEN_MASKED={masked}")
    print(f"THREADS_TOKEN_EXPIRES_AT={expires_at}")
    if effective_user_id:
        print(f"THREADS_USER_ID={effective_user_id}")
    username = str(resolved_user_profile.get("username") or "")
    if username:
        print(f"THREADS_USERNAME={username}")
    return 0


def _print_authorize_url(args: argparse.Namespace, parsed_env: dict[str, str]) -> int:
    app_id = _resolve_setting(args.app_id, parsed_env, "THREADS_APP_ID")
    if not app_id:
        print("ERROR=THREADS_APP_ID is required to generate auth URL")
        return 2
    redirect_uri = str(args.redirect_uri or "").strip()
    if not redirect_uri:
        print("ERROR=--redirect-uri is required with --print-auth-url")
        return 2
    scopes_raw = str(args.scopes or "").strip()
    scopes = [token.strip() for token in scopes_raw.split(",") if token.strip()] if scopes_raw else list(DEFAULT_SCOPES)
    state = str(args.state or "").strip() or secrets.token_urlsafe(16)
    url = _build_authorize_url(app_id=app_id, redirect_uri=redirect_uri, scopes=scopes, state=state)
    print(f"THREADS_AUTH_STATE={state}")
    print(f"THREADS_AUTH_URL={url}")
    return 0


async def _main_async() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="Target env file to read/update")
    parser.add_argument("--mode", default="auto", choices=["auto", "exchange", "refresh"])
    parser.add_argument("--auth-code", default="", help="OAuth code from Threads redirect callback")
    parser.add_argument("--short-lived-token", default="", help="Short-lived token for exchange flow")
    parser.add_argument("--app-id", default="")
    parser.add_argument("--app-secret", default="")
    parser.add_argument("--user-id", default="")
    parser.add_argument("--access-token", default="")
    parser.add_argument("--token-expires-at", default="")
    parser.add_argument("--token-url", default="https://graph.threads.net")
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--refresh-buffer-seconds", type=int, default=6 * 3600)
    parser.add_argument(
        "--resolve-user-id",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Try resolving THREADS_USER_ID from /v1.0/me when missing.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skip-env-write",
        action="store_true",
        help="Do not mutate env file. Useful when storing secrets directly in Infisical.",
    )
    parser.add_argument(
        "--print-infisical-json",
        action="store_true",
        help="Print a machine-readable JSON map of updated secrets for Infisical sync.",
    )
    parser.add_argument(
        "--infisical-json-file",
        default="",
        help="Write updates as JSON object to a file for Infisical sync workflows.",
    )

    parser.add_argument("--print-auth-url", action="store_true")
    parser.add_argument("--redirect-uri", default="")
    parser.add_argument("--scopes", default=",".join(DEFAULT_SCOPES))
    parser.add_argument("--state", default="")

    args = parser.parse_args()

    env_path = Path(args.env_file).expanduser()
    lines, parsed_env = _parse_env(env_path)

    if args.print_auth_url:
        return _print_authorize_url(args, parsed_env)

    return await _exchange_or_refresh(args, env_path=env_path, lines=lines, parsed_env=parsed_env)


def main() -> int:
    import asyncio

    return asyncio.run(_main_async())


if __name__ == "__main__":
    raise SystemExit(main())
