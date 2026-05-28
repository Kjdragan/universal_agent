#!/usr/bin/env python3
"""
Resolve and print the active residential proxy URL.

Provider is selected by PROXY_PROVIDER (default: "dataimpulse"). Credentials
are loaded from Infisical via the project's infisical_loader.

Usage:
    uv run .agents/skills/residential-proxy/scripts/get_proxy_url.py
    # dataimpulse: http://<user>:<pass>@gw.dataimpulse.com:823
    # webshare:    http://<user>:<pass>@p.webshare.io:80
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[4]  # .agents/skills/residential-proxy/scripts -> repo root
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from universal_agent.infisical_loader import initialize_runtime_secrets  # noqa: E402


def _build_webshare_url() -> str | None:
    username = (
        os.getenv("PROXY_USERNAME")
        or os.getenv("WEBSHARE_PROXY_USER")
        or ""
    ).strip()
    password = (
        os.getenv("PROXY_PASSWORD")
        or os.getenv("WEBSHARE_PROXY_PASS")
        or ""
    ).strip()
    host = (
        os.getenv("WEBSHARE_PROXY_HOST")
        or os.getenv("PROXY_HOST")
        or "p.webshare.io"
    ).strip() or "p.webshare.io"
    port = (
        os.getenv("WEBSHARE_PROXY_PORT")
        or os.getenv("PROXY_PORT")
        or "80"
    ).strip() or "80"

    if not username or not password:
        return None
    return f"http://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"


def _build_dataimpulse_url() -> str | None:
    username = (os.getenv("DATAIMPULSE_PROXY_USER") or "").strip()
    password = (os.getenv("DATAIMPULSE_PROXY_PASS") or "").strip()
    host = (
        os.getenv("DATAIMPULSE_PROXY_HOST") or "gw.dataimpulse.com"
    ).strip() or "gw.dataimpulse.com"
    port = (os.getenv("DATAIMPULSE_PROXY_PORT") or "823").strip() or "823"

    if not username or not password:
        return None
    return f"http://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"


def get_proxy_url() -> str | None:
    """Return the residential proxy URL for the active provider.

    Provider selection follows PROXY_PROVIDER (default: "dataimpulse"), matching
    src/universal_agent/youtube_ingest.py:_build_proxy_config().
    """
    initialize_runtime_secrets()
    provider = (os.getenv("PROXY_PROVIDER") or "dataimpulse").strip().lower()
    if provider == "webshare":
        return _build_webshare_url()
    return _build_dataimpulse_url()


def main() -> int:
    url = get_proxy_url()
    if url is None:
        provider = (os.getenv("PROXY_PROVIDER") or "dataimpulse").strip().lower()
        if provider == "webshare":
            missing = "PROXY_USERNAME/PROXY_PASSWORD (or WEBSHARE_PROXY_USER/PASS)"
        else:
            missing = "DATAIMPULSE_PROXY_USER/DATAIMPULSE_PROXY_PASS"
        print(
            f"ERROR: Proxy credentials not found for provider={provider!r}. "
            f"Ensure {missing} are set in Infisical.",
            file=sys.stderr,
        )
        return 1
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
