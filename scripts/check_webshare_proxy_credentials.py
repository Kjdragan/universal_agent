#!/usr/bin/env python3
"""
Offline Webshare credential sanity checker (no network calls).

Purpose:
- Confirm the proxy credentials are actually loaded in runtime env (Infisical/bootstrap).
- Catch common credential-shape mistakes before live proxy probes.

This script does NOT verify upstream proxy availability. Use check_webshare_proxy.py
for live transport checks.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.youtube_ingest import _parse_proxy_locations

CANONICAL_WEBSHARE_HOST = "proxy.webshare.io"
STALE_WEBSHARE_HOST = "p.webshare.io"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _load_local_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
        return
    except Exception:
        pass

    env_path = os.path.join(os.getcwd(), ".env")
    try:
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    key, value = stripped.split("=", 1)
                    key = key.strip()
                    if not key:
                        continue
                    os.environ.setdefault(key, value.strip())
    except Exception:
        pass


def _resolve_proxy_settings() -> dict[str, Any]:
    username = (os.getenv("PROXY_USERNAME") or os.getenv("WEBSHARE_PROXY_USER") or "").strip()
    password = (os.getenv("PROXY_PASSWORD") or os.getenv("WEBSHARE_PROXY_PASS") or "").strip()
    host = (os.getenv("WEBSHARE_PROXY_HOST") or os.getenv("PROXY_HOST") or CANONICAL_WEBSHARE_HOST).strip()
    port_raw = (os.getenv("WEBSHARE_PROXY_PORT") or os.getenv("PROXY_PORT") or "80").strip()
    try:
        port = int(port_raw)
    except Exception:
        port = 80
    if port <= 0 or port > 65535:
        port = 80
    locations = _parse_proxy_locations(
        os.getenv("PROXY_FILTER_IP_LOCATIONS")
        or os.getenv("PROXY_LOCATIONS")
        or os.getenv("YT_PROXY_FILTER_IP_LOCATIONS")
        or os.getenv("WEBSHARE_PROXY_LOCATIONS")
        or ""
    )
    return {
        "username": username,
        "password": password,
        "host": host or CANONICAL_WEBSHARE_HOST,
        "port": port,
        "locations": locations,
    }


def _masked(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 6:
        return "*" * len(text)
    return f"{text[:3]}...{text[-2:]}"


def _issue(level: str, code: str, message: str) -> dict[str, str]:
    return {
        "level": level,
        "code": code,
        "message": message,
    }


def _validate_settings(settings: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    username = str(settings.get("username") or "")
    password = str(settings.get("password") or "")
    host = str(settings.get("host") or "")
    port = int(settings.get("port") or 80)

    if not username or not password:
        issues.append(
            _issue(
                "error",
                "missing_proxy_credentials",
                "Proxy credentials are missing. Set PROXY_USERNAME/PROXY_PASSWORD (or Webshare aliases).",
            )
        )
        return issues

    if host.strip().lower() == STALE_WEBSHARE_HOST:
        issues.append(
            _issue(
                "warning",
                "stale_proxy_host_override",
                "WEBSHARE proxy host resolves to p.webshare.io (stale). Prefer proxy.webshare.io:80.",
            )
        )

    if _EMAIL_RE.match(username):
        issues.append(
            _issue(
                "warning",
                "username_looks_like_account_login",
                "Proxy username looks like an email/login. Webshare dashboard login credentials are often different from proxy auth credentials.",
            )
        )

    if any(ch.isspace() for ch in username):
        issues.append(
            _issue(
                "error",
                "username_contains_whitespace",
                "Proxy username contains whitespace, which is almost certainly invalid.",
            )
        )
    if username.startswith(("http://", "https://")) or ":" in username or "/" in username:
        issues.append(
            _issue(
                "error",
                "username_bad_shape",
                "Proxy username contains URL/path separators. It should be a raw username token only.",
            )
        )

    if any(ch.isspace() for ch in password):
        issues.append(
            _issue(
                "warning",
                "password_contains_whitespace",
                "Proxy password contains whitespace; confirm this is intentional.",
            )
        )

    if port != 80:
        issues.append(
            _issue(
                "warning",
                "non_default_proxy_port",
                f"Proxy port is set to {port}. Canonical default is 80 for proxy.webshare.io.",
            )
        )

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Webshare credential sanity check")
    parser.add_argument(
        "--profile",
        default=None,
        help="Optional deployment profile override for Infisical bootstrap",
    )
    parser.add_argument("--json", action="store_true", help="Reserved for compatibility; output is always JSON")
    args = parser.parse_args()

    _load_local_env()
    bootstrap = initialize_runtime_secrets(
        profile=str(args.profile or os.getenv("UA_DEPLOYMENT_PROFILE") or "local_workstation"),
        force_reload=True,
    )
    settings = _resolve_proxy_settings()
    issues = _validate_settings(settings)

    has_error = any(item.get("level") == "error" for item in issues)
    report = {
        "ok": not has_error,
        "bootstrap": {
            "source": bootstrap.source,
            "environment": bootstrap.environment,
            "runtime_stage": bootstrap.runtime_stage,
            "deployment_profile": bootstrap.deployment_profile,
            "loaded_count": int(bootstrap.loaded_count),
            "errors": list(bootstrap.errors),
        },
        "proxy": {
            "host": settings["host"],
            "port": settings["port"],
            "locations": settings["locations"],
            "username_masked": _masked(str(settings["username"])),
            "password_set": bool(str(settings["password"])),
        },
        "issues": issues,
        "note": (
            "Changing your webshare.io website login password does not guarantee proxy credentials in runtime env changed. "
            "Proxy auth uses PROXY_USERNAME/PROXY_PASSWORD values loaded by runtime bootstrap."
        ),
    }

    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
