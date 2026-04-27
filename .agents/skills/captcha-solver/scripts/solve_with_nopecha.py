#!/usr/bin/env python3
"""
Solve CAPTCHAs using Playwright and NopeCHA extension.

Supports optional --proxy flag to route browser traffic through
a residential proxy (chain with residential-proxy skill).

Usage:
    # Basic (no proxy):
    uv run .agents/skills/captcha-solver/scripts/solve_with_nopecha.py <URL>

    # With residential proxy:
    PROXY_URL=$(uv run .agents/skills/residential-proxy/scripts/get_proxy_url.py)
    uv run .agents/skills/captcha-solver/scripts/solve_with_nopecha.py <URL> --proxy "$PROXY_URL"
"""
import argparse
import os
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright

EXTENSION_PATH = os.path.expanduser("~/.config/nopecha-extension")


def _parse_proxy(proxy_url: str) -> dict:
    """Parse a proxy URL into Playwright's proxy dict format."""
    parsed = urlparse(proxy_url)
    server = f"http://{parsed.hostname}:{parsed.port or 80}"
    result = {"server": server}
    if parsed.username:
        result["username"] = parsed.username
    if parsed.password:
        result["password"] = parsed.password
    return result


def _wait_for_challenge_completion(page, max_wait: int) -> bool:
    """
    Wait for the Cloudflare challenge to be solved and the page to redirect.

    Instead of a dumb sleep, we poll the page for signs that:
    1. The challenge interstitial ("Just a moment") has disappeared
    2. OR the URL has changed (redirect completed)
    3. OR we find a "Verification successful" signal followed by new content

    Returns True if we believe the challenge was bypassed.
    """
    start = time.monotonic()
    original_url = page.url
    challenge_solved = False
    redirected = False

    print(f"Polling for challenge completion (max {max_wait}s)...", file=sys.stderr)

    while time.monotonic() - start < max_wait:
        time.sleep(2)  # Poll every 2 seconds

        try:
            current_url = page.url
            content = page.content()
        except Exception:
            # Page might be navigating
            time.sleep(1)
            continue

        # Check if verification was successful
        if "Verification successful" in content or "success" in content.lower():
            if not challenge_solved:
                elapsed = round(time.monotonic() - start, 1)
                print(f"  ✓ Challenge solved at {elapsed}s", file=sys.stderr)
                challenge_solved = True

        # Check if the page has redirected away from the challenge
        if current_url != original_url:
            elapsed = round(time.monotonic() - start, 1)
            print(f"  ✓ Redirected to {current_url} at {elapsed}s", file=sys.stderr)
            redirected = True
            # Give the new page a moment to load
            time.sleep(3)
            break

        # Check if the challenge interstitial is gone
        if challenge_solved and "Just a moment" not in content:
            elapsed = round(time.monotonic() - start, 1)
            print(f"  ✓ Interstitial cleared at {elapsed}s", file=sys.stderr)
            redirected = True
            time.sleep(2)
            break

        # If challenge is solved, try to wait for navigation
        if challenge_solved:
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
                elapsed = round(time.monotonic() - start, 1)
                print(f"  ✓ Network idle at {elapsed}s", file=sys.stderr)
                # Check once more if content changed
                new_content = page.content()
                if "Just a moment" not in new_content:
                    redirected = True
                    break
            except PlaywrightTimeout:
                pass  # Keep polling

    elapsed = round(time.monotonic() - start, 1)
    if challenge_solved and redirected:
        print(f"  ✓ Challenge bypass complete ({elapsed}s total)", file=sys.stderr)
    elif challenge_solved:
        print(f"  ⚠ Challenge solved but redirect may not have completed ({elapsed}s)", file=sys.stderr)
    else:
        print(f"  ✗ Challenge not solved within {elapsed}s", file=sys.stderr)

    return challenge_solved


def main():
    parser = argparse.ArgumentParser(description="Solve CAPTCHAs using Playwright and NopeCHA.")
    parser.add_argument("url", help="Target URL to visit and bypass.")
    parser.add_argument("--out-html", help="File to save the post-bypassed HTML.")
    parser.add_argument("--out-cookies", help="File to save the post-bypassed storage state/cookies.")
    parser.add_argument("--wait-time", type=int, default=30,
                        help="Max time to wait (seconds) for CAPTCHA solve + redirect. Default: 30.")
    parser.add_argument("--proxy", help="Proxy URL (e.g. http://user:pass@host:port). Use with residential-proxy skill.")
    args = parser.parse_args()

    if not os.path.isdir(EXTENSION_PATH):
        print(f"Error: Extension path not found at {EXTENSION_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"Launching Chromium with NopeCHA extension...", file=sys.stderr)

    proxy_config = None
    if args.proxy:
        proxy_config = _parse_proxy(args.proxy)
        print(f"Using proxy: {proxy_config['server']} (credentials hidden)", file=sys.stderr)

    with sync_playwright() as p:
        browser_args = [
            f"--disable-extensions-except={EXTENSION_PATH}",
            f"--load-extension={EXTENSION_PATH}",
            "--headless=new",  # New headless mode allows extension loading
        ]

        import tempfile
        with tempfile.TemporaryDirectory() as user_data_dir:
            launch_kwargs = {
                "headless": False,  # Must be False for persistent_context; --headless=new in args handles it
                "args": browser_args,
                "ignore_default_args": ["--headless"],
            }

            if proxy_config:
                launch_kwargs["proxy"] = proxy_config

            browser = p.chromium.launch_persistent_context(
                user_data_dir,
                **launch_kwargs,
            )

            page = browser.new_page()
            print(f"Navigating to {args.url} ...", file=sys.stderr)
            try:
                page.goto(args.url, timeout=60000, wait_until="domcontentloaded")
            except Exception as e:
                print(f"Navigation warning (may be expected with CAPTCHA): {e}", file=sys.stderr)

            # Smart wait: poll for challenge completion instead of dumb sleep
            challenge_solved = _wait_for_challenge_completion(page, args.wait_time)

            if args.out_html:
                content = page.content()
                with open(args.out_html, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"HTML saved to {args.out_html} ({len(content)} chars)", file=sys.stderr)

            if args.out_cookies:
                browser.storage_state(path=args.out_cookies)
                print(f"Cookies saved to {args.out_cookies}", file=sys.stderr)

            browser.close()

            if challenge_solved:
                print("Done — challenge was solved.", file=sys.stderr)
            else:
                print("Done — challenge may NOT have been solved. Check output.", file=sys.stderr)
                sys.exit(2)


if __name__ == "__main__":
    main()
