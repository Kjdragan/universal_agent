#!/usr/bin/env python3
"""
Fetch a URL through the Webshare rotating residential proxy.

One-shot utility: loads Infisical credentials, builds the proxy URL,
fetches the target URL via HTTPS CONNECT through the proxy, and
prints the response body (or saves to --out).

Usage:
    uv run .agents/skills/residential-proxy/scripts/proxy_fetch.py https://example.com
    uv run .agents/skills/residential-proxy/scripts/proxy_fetch.py https://example.com --out /tmp/page.html
    uv run .agents/skills/residential-proxy/scripts/proxy_fetch.py https://example.com --timeout 30
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

# Import the sibling get_proxy_url module
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from get_proxy_url import get_proxy_url  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch a URL through the Webshare residential proxy."
    )
    parser.add_argument("url", help="Target URL to fetch.")
    parser.add_argument("--out", help="File path to save the response body.")
    parser.add_argument(
        "--timeout", type=float, default=20.0,
        help="Request timeout in seconds (default: 20).",
    )
    parser.add_argument(
        "--headers-only", action="store_true",
        help="Print response status and headers only, skip body.",
    )
    args = parser.parse_args()

    proxy_url = get_proxy_url()
    if proxy_url is None:
        print(
            "ERROR: Proxy credentials not found. "
            "Ensure PROXY_USERNAME and PROXY_PASSWORD are set in Infisical.",
            file=sys.stderr,
        )
        return 1

    # Redact password for logging
    redacted = proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url
    print(f"Using proxy: http://***@{redacted}", file=sys.stderr)
    print(f"Fetching: {args.url}", file=sys.stderr)

    proxy_handler = urllib.request.ProxyHandler(
        {"http": proxy_url, "https": proxy_url}
    )
    opener = urllib.request.build_opener(proxy_handler)
    request = urllib.request.Request(
        args.url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        },
        method="GET",
    )

    started = time.monotonic()
    try:
        with opener.open(
            request, timeout=max(5.0, float(args.timeout))
        ) as response:
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            status = getattr(response, "status", 200)
            print(
                f"Status: {status} | Latency: {elapsed_ms}ms",
                file=sys.stderr,
            )

            if args.headers_only:
                for header, value in response.getheaders():
                    print(f"{header}: {value}")
                return 0

            body = response.read().decode("utf-8", errors="replace")

            if args.out:
                with open(args.out, "w", encoding="utf-8") as f:
                    f.write(body)
                print(f"Saved {len(body)} chars to {args.out}", file=sys.stderr)
            else:
                print(body)

            return 0

    except urllib.error.HTTPError as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        payload = exc.read().decode("utf-8", errors="replace")
        print(
            f"HTTP Error {exc.code} after {elapsed_ms}ms",
            file=sys.stderr,
        )
        print(payload[:2000], file=sys.stderr)
        return 1

    except Exception as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        print(
            f"Request failed after {elapsed_ms}ms: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
