#!/usr/bin/env python3
"""Automated OAuth2 setup for the YouTube Data API.

This script:
1. Starts a local HTTP server to receive the OAuth callback
2. Opens a browser for you to authorize access
3. Exchanges the authorization code for a refresh token
4. Saves everything securely to Infisical

Usage:
  uv run src/universal_agent/scripts/youtube_oauth2_setup.py
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

# Fix python path for local execution
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from universal_agent.infisical_loader import upsert_infisical_secret

# ── OAuth Client credentials loaded from Infisical ─────────────────────
# These are stored as YOUTUBE_OAUTH_CLIENT_ID and YOUTUBE_OAUTH_CLIENT_SECRET
# in the Infisical secret store.  Run `initialize_runtime_secrets()` or
# ensure these env vars are populated before executing this script.
from universal_agent.infisical_loader import initialize_runtime_secrets as _init_secrets
_init_secrets()

CLIENT_ID = os.environ.get("YOUTUBE_OAUTH_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRET", "").strip()

if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ YOUTUBE_OAUTH_CLIENT_ID and YOUTUBE_OAUTH_CLIENT_SECRET must be set in Infisical.")
    print("   Run: infisical secrets set YOUTUBE_OAUTH_CLIENT_ID <value>")
    print("   Run: infisical secrets set YOUTUBE_OAUTH_CLIENT_SECRET <value>")
    sys.exit(1)

OAUTH2_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH2_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube"
REDIRECT_PORT = 8080
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}"


class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """Tiny HTTP handler that captures the OAuth callback code."""

    auth_code: str | None = None
    error: str | None = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            _OAuthCallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>&#10004; Authorization successful!</h2>"
                b"<p>You can close this tab and return to the terminal.</p></body></html>"
            )
        elif "error" in params:
            _OAuthCallbackHandler.error = params["error"][0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                f"<html><body><h2>Authorization failed: {params['error'][0]}</h2></body></html>".encode()
            )
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress noisy request logs


def main():
    print("\n" + "=" * 70)
    print("  YOUTUBE OAUTH 2.0 SETUP")
    print("=" * 70)

    # Build the consent URL
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": YOUTUBE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"{OAUTH2_AUTH_URL}?{urllib.parse.urlencode(params)}"

    # Start local callback server
    server = http.server.HTTPServer(("localhost", REDIRECT_PORT), _OAuthCallbackHandler)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    print(f"\n🌐 Opening browser for Google authorization...")
    print(f"   (Listening on http://localhost:{REDIRECT_PORT} for callback)\n")

    try:
        webbrowser.open(auth_url)
    except Exception:
        print("   Could not auto-open browser. Please open this URL manually:\n")
        print(f"   {auth_url}\n")

    print("   Waiting for you to authorize in the browser...")
    server_thread.join(timeout=120)
    server.server_close()

    if _OAuthCallbackHandler.error:
        print(f"\n❌ Authorization failed: {_OAuthCallbackHandler.error}")
        sys.exit(1)

    if not _OAuthCallbackHandler.auth_code:
        print("\n❌ Timed out waiting for authorization. Please try again.")
        sys.exit(1)

    code = _OAuthCallbackHandler.auth_code
    print("\n✅ Authorization code received!")

    # Exchange code for tokens
    print("🔄 Exchanging code for tokens...")
    response = httpx.post(
        OAUTH2_TOKEN_URL,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=15.0,
    )

    if response.status_code != 200:
        print(f"❌ Token exchange failed ({response.status_code}): {response.text}")
        sys.exit(1)

    token_data = response.json()
    refresh_token = token_data.get("refresh_token")

    if not refresh_token:
        print("❌ No refresh token received.")
        print("   Go to https://myaccount.google.com/permissions, revoke 'Universal Agent', and re-run.")
        sys.exit(1)

    print("✅ Refresh Token obtained!")

    # Verify it works by listing playlists
    print("\n🧪 Testing YouTube API access...")
    access_token = token_data.get("access_token", "")
    yt_resp = httpx.get(
        "https://youtube.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15.0,
    )
    if yt_resp.status_code == 200:
        data = yt_resp.json()
        items = data.get("items", [])
        if items:
            channel_title = items[0].get("snippet", {}).get("title", "Unknown")
            print(f"   ✅ Connected to YouTube channel: {channel_title}")
        else:
            print("   ✅ YouTube API access works (no channel found, but auth is valid)")
    else:
        print(f"   ⚠️  YouTube API returned {yt_resp.status_code} — auth may still work for playlists")

    # Save to Infisical
    print("\n🔐 Saving credentials to Infisical...")
    ok1 = upsert_infisical_secret("YOUTUBE_OAUTH_CLIENT_ID", CLIENT_ID)
    ok2 = upsert_infisical_secret("YOUTUBE_OAUTH_CLIENT_SECRET", CLIENT_SECRET)
    ok3 = upsert_infisical_secret("YOUTUBE_OAUTH_REFRESH_TOKEN", refresh_token)

    if ok1 and ok2 and ok3:
        print("\n🎉 All credentials saved to Infisical successfully!")
        print("   The Universal Agent can now manage your YouTube playlists autonomously.")
    else:
        print("\n⚠️  Some credentials could not be saved to Infisical (saved to local env only).")
        print("   Ensure INFISICAL_CLIENT_ID, INFISICAL_CLIENT_SECRET, INFISICAL_PROJECT_ID are set.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
