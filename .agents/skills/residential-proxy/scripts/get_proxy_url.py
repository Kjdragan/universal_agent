#!/usr/bin/env python3
"""
Resolve and print the Webshare rotating residential proxy URL.

Loads credentials from Infisical (via the project's infisical_loader),
then constructs and prints the proxy URL to stdout.

Usage:
    uv run .agents/skills/residential-proxy/scripts/get_proxy_url.py
    # prints: http://rotatingproxyua-rotate:<password>@p.webshare.io:80
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


def get_proxy_url() -> str | None:
    """Load Infisical secrets and return the Webshare residential proxy URL."""
    initialize_runtime_secrets()

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
    ).strip()
    port = (
        os.getenv("WEBSHARE_PROXY_PORT")
        or os.getenv("PROXY_PORT")
        or "80"
    ).strip()

    if not username or not password:
        return None

    user_encoded = quote(username, safe="")
    pass_encoded = quote(password, safe="")
    return f"http://{user_encoded}:{pass_encoded}@{host}:{port}"


def main() -> int:
    url = get_proxy_url()
    if url is None:
        print(
            "ERROR: Proxy credentials not found. "
            "Ensure PROXY_USERNAME and PROXY_PASSWORD are set in Infisical.",
            file=sys.stderr,
        )
        return 1
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
