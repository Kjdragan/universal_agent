#!/usr/bin/env python3
"""
Update Webshare rotating residential proxy credentials in Infisical.

Usage:
    # Interactively prompt for credentials:
    uv run python scripts/update_webshare_proxy_credentials.py

    # Pass credentials as args (for automation):
    uv run python scripts/update_webshare_proxy_credentials.py \\
        --username <user> --password <pass>

    # Update both development AND production environments:
    uv run python scripts/update_webshare_proxy_credentials.py \\
        --username <user> --password <pass> --environments development production

    # Dry-run (show what would be updated):
    uv run python scripts/update_webshare_proxy_credentials.py --dry-run

After updating credentials, re-run:
    uv run python scripts/check_webshare_proxy.py
to verify connectivity.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

API_URL_DEFAULT = "https://app.infisical.com"
DEFAULT_ENVIRONMENTS = ["development", "production"]
PROXY_SECRET_KEYS = ["PROXY_USERNAME", "PROXY_PASSWORD"]


def _load_dotenv() -> None:
    dotenv_path = REPO_ROOT / ".env"
    if not dotenv_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path)
        return
    except Exception:
        pass
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


def _upsert_secrets(
    api_url: str,
    token: str,
    project_id: str,
    environment: str,
    secrets: dict[str, str],
    *,
    dry_run: bool = False,
) -> dict[str, str]:
    """Upsert secrets, returning a dict of {key: 'created'|'updated'|'unchanged'}."""
    import httpx

    # Fetch existing
    resp = httpx.get(
        f"{api_url}/api/v3/secrets/raw",
        params={
            "workspaceId": project_id,
            "environment": environment,
            "secretPath": "/",
            "recursive": "false",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    existing: dict[str, str] = {}
    for item in (resp.json() or {}).get("secrets", []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("secretKey") or "").strip()
        if key:
            existing[key] = str(item.get("secretValue") or "")

    to_create = {k: v for k, v in secrets.items() if k not in existing}
    to_update = {k: v for k, v in secrets.items() if k in existing and existing[k] != v}
    unchanged = [k for k in secrets if k in existing and existing[k] == secrets[k]]

    result: dict[str, str] = {k: "unchanged" for k in unchanged}

    if dry_run:
        for k in to_create:
            result[k] = "would_create"
        for k in to_update:
            result[k] = "would_update"
        return result

    if to_create:
        r = httpx.post(
            f"{api_url}/api/v4/secrets/batch",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "projectId": project_id,
                "environment": environment,
                "secretPath": "/",
                "secrets": [{"secretKey": k, "secretValue": v} for k, v in to_create.items()],
            },
            timeout=30.0,
        )
        r.raise_for_status()
        for k in to_create:
            result[k] = "created"

    if to_update:
        r = httpx.patch(
            f"{api_url}/api/v4/secrets/batch",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "projectId": project_id,
                "environment": environment,
                "secretPath": "/",
                "secrets": [{"secretKey": k, "secretValue": v} for k, v in to_update.items()],
            },
            timeout=30.0,
        )
        r.raise_for_status()
        for k in to_update:
            result[k] = "updated"

    return result


def _verify_proxy() -> dict:
    """Run a quick live proxy probe using the new credentials."""
    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets
        initialize_runtime_secrets(force_reload=True)
    except Exception:
        pass

    import urllib.request

    from universal_agent.youtube_ingest import (
        _build_webshare_proxy_config,  # type: ignore
    )

    proxy_config, proxy_mode = _build_webshare_proxy_config()

    if proxy_config is None:
        return {"ok": False, "reason": f"proxy_config_none mode={proxy_mode}"}

    proxy_url = getattr(proxy_config, "url", None) or f"http://{os.getenv('PROXY_USERNAME')}:{os.getenv('PROXY_PASSWORD')}@proxy.webshare.io:80"

    try:
        proxy_handler = urllib.request.ProxyHandler({
            "http": str(proxy_url),
            "https": str(proxy_url),
        })
        opener = urllib.request.build_opener(proxy_handler)
        with opener.open("https://api.ipify.org?format=json", timeout=20) as resp:
            data = json.loads(resp.read().decode())
        return {"ok": True, "external_ip": data.get("ip", "unknown")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Update Webshare proxy credentials in Infisical")
    parser.add_argument("--username", default="", help="Webshare proxy username (PROXY_USERNAME)")
    parser.add_argument("--password", default="", help="Webshare proxy password (PROXY_PASSWORD)")
    parser.add_argument(
        "--environments",
        nargs="+",
        default=DEFAULT_ENVIRONMENTS,
        help=f"Infisical environments to update (default: {DEFAULT_ENVIRONMENTS})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without writing")
    parser.add_argument("--no-verify", action="store_true", help="Skip live proxy connectivity verification")
    args = parser.parse_args()

    _load_dotenv()

    username = args.username.strip() or os.getenv("PROXY_USERNAME", "").strip()
    password = args.password.strip() or os.getenv("PROXY_PASSWORD", "").strip()

    if not username:
        print("Enter Webshare proxy username (from Webshare dashboard > Proxy > Credentials):")
        username = input("PROXY_USERNAME: ").strip()
    if not password:
        password = getpass.getpass("PROXY_PASSWORD: ").strip()

    if not username or not password:
        print("ERROR: Both username and password are required.", file=sys.stderr)
        return 1

    try:
        client_id = _require_env("INFISICAL_CLIENT_ID")
        client_secret = _require_env("INFISICAL_CLIENT_SECRET")
        project_id = _require_env("INFISICAL_PROJECT_ID")
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    api_url = (os.getenv("INFISICAL_API_URL") or API_URL_DEFAULT).rstrip("/")
    secrets = {"PROXY_USERNAME": username, "PROXY_PASSWORD": password}

    print(f"\nInfosical API: {api_url}")
    print(f"Project: {project_id}")
    print(f"Environments: {args.environments}")
    print(f"Keys to upsert: {list(secrets.keys())}")
    print(f"Username: {username[:4]}...{username[-2:] if len(username) > 6 else '***'}")
    print(f"Dry-run: {args.dry_run}\n")

    try:
        token = _authenticate(api_url, client_id, client_secret)
    except Exception as exc:
        print(f"ERROR: Infisical authentication failed: {exc}", file=sys.stderr)
        return 1

    overall_ok = True
    for env in args.environments:
        try:
            result = _upsert_secrets(api_url, token, project_id, env, secrets, dry_run=args.dry_run)
            status_parts = ", ".join(f"{k}={v}" for k, v in result.items())
            verb = "WOULD UPDATE" if args.dry_run else "UPDATED"
            print(f"[{env}] {verb}: {status_parts}")
        except Exception as exc:
            print(f"[{env}] ERROR: {exc}", file=sys.stderr)
            overall_ok = False

    if not overall_ok:
        return 1

    if args.dry_run:
        print("\nDry-run complete. Re-run without --dry-run to apply.")
        return 0

    # Update local env for verification
    os.environ["PROXY_USERNAME"] = username
    os.environ["PROXY_PASSWORD"] = password

    if not args.no_verify:
        print("\nVerifying proxy connectivity with new credentials...")
        try:
            probe = _verify_proxy()
            if probe.get("ok"):
                print(f"✅ Proxy OK — external IP: {probe.get('external_ip', 'unknown')}")
            else:
                print(f"❌ Proxy verification FAILED: {probe.get('error') or probe.get('reason')}")
                print("   Credentials were saved. Re-check them in the Webshare dashboard.")
        except Exception as exc:
            print(f"⚠️  Proxy verification error (credentials may still be OK): {exc}")

    print("\nNext steps:")
    print("  1. Deploy to staging: merge to develop branch")
    print("  2. Verify on VPS: uv run python scripts/check_webshare_proxy.py")
    print("  3. Any failed videos will be retried on the next playlist poll cycle")
    print("     (proxy_connect_failed videos are held in cooldown — clear with service restart)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
