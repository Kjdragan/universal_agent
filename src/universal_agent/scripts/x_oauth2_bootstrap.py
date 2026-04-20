"""Bootstrap X OAuth2 user-context tokens.

This script implements the official Authorization Code + PKCE flow:

1. Print an authorization URL and persist PKCE state.
2. Exchange a returned callback URL/code for access and refresh tokens.
3. Store tokens in Infisical development, production, and local by default.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.infisical_loader import initialize_runtime_secrets


DEFAULT_SCOPES = ("tweet.read", "users.read", "offline.access")
TOKEN_URL = "https://api.x.com/2/oauth2/token"
AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap X OAuth2 user tokens for Universal Agent.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    auth = sub.add_parser("authorize-url", help="Create and print the X authorization URL.")
    auth.add_argument("--scope", action="append", default=[], help="OAuth scope to request; repeatable.")
    auth.add_argument("--redirect-uri", default="", help="Override redirect URI.")
    auth.add_argument("--profile", default="local_workstation")

    listen = sub.add_parser("listen", help="Print authorization URL, listen for callback, exchange immediately, and store tokens.")
    listen.add_argument("--scope", action="append", default=[], help="OAuth scope to request; repeatable.")
    listen.add_argument("--redirect-uri", default="", help="Override redirect URI.")
    listen.add_argument("--environment", action="append", default=[], help="Infisical environment to store in; repeatable.")
    listen.add_argument("--profile", default="local_workstation")
    listen.add_argument("--timeout-seconds", type=int, default=180, help="How long to wait for the browser callback.")

    exchange = sub.add_parser("exchange", help="Exchange a callback URL or auth code for tokens and store them.")
    group = exchange.add_mutually_exclusive_group(required=True)
    group.add_argument("--callback-url", default="", help="Full redirected callback URL from X.")
    group.add_argument("--code", default="", help="Raw authorization code.")
    exchange.add_argument("--state", default="", help="State value when using --code.")
    exchange.add_argument("--redirect-uri", default="", help="Override redirect URI.")
    exchange.add_argument("--environment", action="append", default=[], help="Infisical environment to store in; repeatable.")
    exchange.add_argument("--profile", default="local_workstation")
    exchange.add_argument("--no-store", action="store_true", help="Exchange and print metadata only; do not write Infisical.")

    refresh = sub.add_parser("refresh", help="Refresh the stored OAuth2 access token.")
    refresh.add_argument("--environment", action="append", default=[], help="Infisical environment to store in; repeatable.")
    refresh.add_argument("--profile", default="local_workstation")
    refresh.add_argument("--no-store", action="store_true")

    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    initialize_runtime_secrets(profile=args.profile)
    if args.cmd == "authorize-url":
        payload = create_authorization_url(
            scopes=tuple(args.scope or DEFAULT_SCOPES),
            redirect_uri=args.redirect_uri or _redirect_uri_from_env(),
        )
        print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
        print("\nOpen this URL in your browser, authorize the app, then run:")
        print("PYTHONPATH=src uv run python -m universal_agent.scripts.x_oauth2_bootstrap exchange --callback-url '<PASTE_CALLBACK_URL>'")
        return 0
    if args.cmd == "exchange":
        code, state = _code_state_from_args(args)
        token_payload = exchange_code_for_token(
            code=code,
            state=state,
            redirect_uri=args.redirect_uri or _redirect_uri_from_env(),
        )
        saved = []
        if not args.no_store:
            saved = store_token_payload(token_payload, environments=args.environment or ["development", "production", "local"])
        print(_safe_token_result(token_payload, saved=saved))
        return 0
    if args.cmd == "listen":
        redirect_uri = args.redirect_uri or _redirect_uri_from_env()
        payload = create_authorization_url(scopes=tuple(args.scope or DEFAULT_SCOPES), redirect_uri=redirect_uri)
        print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
        print("\nOpen the authorization_url in your browser now. Waiting for callback...")
        code, state = _listen_for_callback(redirect_uri=redirect_uri, timeout_seconds=max(30, int(args.timeout_seconds or 180)))
        token_payload = exchange_code_for_token(code=code, state=state, redirect_uri=redirect_uri)
        saved = store_token_payload(token_payload, environments=args.environment or ["development", "production", "local"])
        print(_safe_token_result(token_payload, saved=saved))
        return 0
    if args.cmd == "refresh":
        refresh_token = str(os.getenv("X_OAUTH2_REFRESH_TOKEN") or "").strip()
        if not refresh_token:
            raise RuntimeError("missing X_OAUTH2_REFRESH_TOKEN")
        token_payload = refresh_access_token(refresh_token)
        saved = []
        if not args.no_store:
            saved = store_token_payload(token_payload, environments=args.environment or ["development", "production", "local"])
        print(_safe_token_result(token_payload, saved=saved))
        return 0
    raise RuntimeError(f"unknown command: {args.cmd}")


def create_authorization_url(*, scopes: tuple[str, ...], redirect_uri: str) -> dict[str, Any]:
    client_id = _required_env("X_OAUTH2_CLIENT_ID", fallback="CLIENT_ID")
    code_verifier = _new_code_verifier()
    state = secrets.token_urlsafe(32)
    code_challenge = _code_challenge(code_verifier)
    payload = {
        "created_at": int(time.time()),
        "state": state,
        "code_verifier": code_verifier,
        "code_challenge": code_challenge,
        "redirect_uri": redirect_uri,
        "scopes": list(scopes),
    }
    _pending_state_path().write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return {
        "authorization_url": f"{AUTHORIZE_URL}?{query}",
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state_file": str(_pending_state_path()),
    }


def exchange_code_for_token(*, code: str, state: str, redirect_uri: str) -> dict[str, Any]:
    pending = _load_pending_state()
    expected_state = str(pending.get("state") or "")
    if expected_state and state and state != expected_state:
        raise RuntimeError("OAuth state mismatch")
    code_verifier = str(pending.get("code_verifier") or "")
    if not code_verifier:
        raise RuntimeError("missing pending OAuth code_verifier; run authorize-url first")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    headers.update(_client_basic_auth_header())
    data = {
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    return _post_token(data=data, headers=headers)


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    headers.update(_client_basic_auth_header())
    data = {
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    return _post_token(data=data, headers=headers)


def _listen_for_callback(*, redirect_uri: str, timeout_seconds: int) -> tuple[str, str]:
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 80)
    expected_path = parsed.path or "/oauth/callback"
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            incoming = urlparse(self.path)
            qs = parse_qs(incoming.query)
            if incoming.path != expected_path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Unknown OAuth callback path.")
                return
            result["code"] = (qs.get("code") or [""])[0]
            result["state"] = (qs.get("state") or [""])[0]
            result["error"] = (qs.get("error") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            if result.get("code"):
                self.wfile.write(b"X OAuth callback received. You can return to the terminal.")
            else:
                self.wfile.write(b"X OAuth callback did not include a code. Return to the terminal.")

    server = HTTPServer((host, port), Handler)
    server.timeout = timeout_seconds
    server.handle_request()
    server.server_close()
    if result.get("error"):
        raise RuntimeError(f"X OAuth authorization failed: {result['error']}")
    if not result.get("code"):
        raise RuntimeError("timed out waiting for OAuth callback code")
    return result["code"], result.get("state", "")


def store_token_payload(payload: dict[str, Any], *, environments: list[str]) -> list[str]:
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("token response missing access_token")
    expires_in = int(payload.get("expires_in") or 0)
    expires_at = str(int(time.time()) + expires_in) if expires_in else ""
    secrets_to_store = {
        "X_OAUTH2_ACCESS_TOKEN": access_token,
        "X_OAUTH2_REFRESH_TOKEN": str(payload.get("refresh_token") or os.getenv("X_OAUTH2_REFRESH_TOKEN") or "").strip(),
        "X_OAUTH2_TOKEN_TYPE": str(payload.get("token_type") or "bearer"),
        "X_OAUTH2_SCOPE": str(payload.get("scope") or ""),
        "X_OAUTH2_EXPIRES_AT": expires_at,
    }
    _store_infisical_secrets(secrets_to_store, environments=environments)
    return sorted(environments)


def _post_token(*, data: dict[str, str], headers: dict[str, str]) -> dict[str, Any]:
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(TOKEN_URL, headers=headers, data=data)
    try:
        payload = resp.json()
    except Exception:
        payload = {}
    if resp.status_code >= 400:
        detail = str(payload.get("error_description") or payload.get("error") or payload).strip()
        raise RuntimeError(f"X OAuth2 token exchange failed: HTTP {resp.status_code}{(': ' + detail) if detail else ''}")
    if not isinstance(payload, dict):
        raise RuntimeError("X OAuth2 token response was not a JSON object")
    return payload


def _store_infisical_secrets(secrets_payload: dict[str, str], *, environments: list[str]) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo_root / "scripts"))
    from infisical_upsert_secret import (  # type: ignore
        API_URL_DEFAULT,
        _authenticate,
        _batch_write,
        _fetch_secrets,
        _load_dotenv_into_environ,
        _require_env,
    )

    _load_dotenv_into_environ()
    api_url = (os.getenv("INFISICAL_API_URL") or API_URL_DEFAULT).strip().rstrip("/")
    token = _authenticate(api_url, _require_env("INFISICAL_CLIENT_ID"), _require_env("INFISICAL_CLIENT_SECRET"))
    project_id = _require_env("INFISICAL_PROJECT_ID")
    for env_name in environments:
        existing = _fetch_secrets(api_url, token, project_id, env_name, "/")
        create = {key: value for key, value in secrets_payload.items() if key not in existing and value}
        update = {key: value for key, value in secrets_payload.items() if key in existing and existing[key] != value and value}
        _batch_write(api_url, token, project_id, env_name, "/", method="POST", secrets=create)
        _batch_write(api_url, token, project_id, env_name, "/", method="PATCH", secrets=update)


def _code_state_from_args(args: argparse.Namespace) -> tuple[str, str]:
    if args.callback_url:
        parsed = urlparse(args.callback_url)
        qs = parse_qs(parsed.query)
        code = (qs.get("code") or [""])[0]
        state = (qs.get("state") or [""])[0]
    else:
        code = args.code
        state = args.state
    if not code:
        raise RuntimeError("missing OAuth authorization code")
    return code, state


def _redirect_uri_from_env() -> str:
    explicit = str(os.getenv("X_OAUTH2_REDIRECT_URI") or "").strip()
    if explicit:
        return explicit
    host = str(os.getenv("X_OAUTH_CALLBACK_HOST") or "127.0.0.1").strip()
    port = str(os.getenv("X_OAUTH_CALLBACK_PORT") or "8976").strip()
    path = str(os.getenv("X_OAUTH_CALLBACK_PATH") or "/oauth/callback").strip() or "/oauth/callback"
    if not path.startswith("/"):
        path = "/" + path
    return f"http://{host}:{port}{path}"


def _new_code_verifier() -> str:
    return secrets.token_urlsafe(64)[:128]


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _client_basic_auth_header() -> dict[str, str]:
    client_id = _required_env("X_OAUTH2_CLIENT_ID", fallback="CLIENT_ID")
    client_secret = _required_env("X_OAUTH2_CLIENT_SECRET", fallback="CLIENT_SECRET")
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


def _required_env(name: str, *, fallback: str = "") -> str:
    value = str(os.getenv(name) or "").strip()
    if not value and fallback:
        value = str(os.getenv(fallback) or "").strip()
    if not value:
        raise RuntimeError(f"missing {name}")
    return value


def _pending_state_path() -> Path:
    root = resolve_artifacts_dir() / "proactive" / "claude_code_intel" / "oauth2"
    root.mkdir(parents=True, exist_ok=True)
    return root / "pending_oauth2.json"


def _load_pending_state() -> dict[str, Any]:
    path = _pending_state_path()
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_token_result(payload: dict[str, Any], *, saved: list[str]) -> str:
    return json.dumps(
        {
            "ok": True,
            "token_type": payload.get("token_type") or "",
            "expires_in": payload.get("expires_in") or 0,
            "scope": payload.get("scope") or "",
            "has_access_token": bool(payload.get("access_token")),
            "has_refresh_token": bool(payload.get("refresh_token")),
            "saved_environments": saved,
        },
        indent=2,
        ensure_ascii=True,
        sort_keys=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
