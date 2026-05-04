#!/usr/bin/env python3
"""One-shot: discover the user's "<Day> Digest" YouTube playlists and
upsert their IDs to Infisical as <DAY>_YT_PLAYLIST.

Eliminates the need to manually copy/paste 7 playlist IDs.  Uses the
existing YouTube OAuth2 credentials in Infisical (the same ones backing
`youtube_playlist_manager.py`) to call the YouTube Data API v3
`/playlists?mine=true` endpoint.

Usage:
    uv run src/universal_agent/scripts/youtube_provision_digest_playlists.py
    # add --dry-run to preview without writing to Infisical

Exits 0 on full success (all 7 days mapped + upserted).  Exits 1 on any
missing day or auth failure so you notice immediately.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

import httpx

# Fix python path for local execution (mirrors youtube_oauth2_setup.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from universal_agent.infisical_loader import initialize_runtime_secrets  # noqa: E402

logger = logging.getLogger(__name__)

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
OAUTH2_TOKEN_URL = "https://oauth2.googleapis.com/token"

DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
DIGEST_PATTERN = re.compile(r"^\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+digest\s*$", re.IGNORECASE)


def _get_access_token() -> str:
    """Exchange the stored refresh token for a short-lived access token."""
    client_id = (os.getenv("YOUTUBE_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("YOUTUBE_OAUTH_CLIENT_SECRET") or "").strip()
    refresh_token = (os.getenv("YOUTUBE_OAUTH_REFRESH_TOKEN") or "").strip()
    if not (client_id and client_secret and refresh_token):
        raise RuntimeError(
            "Missing YOUTUBE_OAUTH_CLIENT_ID / YOUTUBE_OAUTH_CLIENT_SECRET / YOUTUBE_OAUTH_REFRESH_TOKEN. "
            "Run youtube_oauth2_setup.py first."
        )
    resp = httpx.post(
        OAUTH2_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    token = (resp.json().get("access_token") or "").strip()
    if not token:
        raise RuntimeError("OAuth response did not include access_token.")
    return token


def _list_my_playlists(access_token: str) -> list[dict]:
    """Page through /playlists?mine=true and return all playlist dicts."""
    out: list[dict] = []
    page_token: str | None = None
    headers = {"Authorization": f"Bearer {access_token}"}
    with httpx.Client(timeout=20.0) as client:
        while True:
            params = {"part": "snippet", "mine": "true", "maxResults": "50"}
            if page_token:
                params["pageToken"] = page_token
            r = client.get(f"{YOUTUBE_API_BASE}/playlists", headers=headers, params=params)
            r.raise_for_status()
            body = r.json()
            out.extend(body.get("items") or [])
            page_token = body.get("nextPageToken")
            if not page_token:
                break
    return out


def _match_digest_playlists(playlists: list[dict]) -> dict[str, str]:
    """Return {DAY_UPPER: playlist_id} for any playlist titled '<Day> Digest'."""
    matches: dict[str, str] = {}
    for pl in playlists:
        title = (pl.get("snippet", {}).get("title") or "").strip()
        m = DIGEST_PATTERN.match(title)
        if not m:
            continue
        day = m.group(1).upper()
        pl_id = pl.get("id")
        if not pl_id:
            continue
        if day in matches:
            logger.warning("Multiple playlists matched %s Digest; keeping first (%s).", day, matches[day])
            continue
        matches[day] = pl_id
    return matches


def _upsert_to_infisical(secrets: dict[str, str], dry_run: bool) -> None:
    """Upsert all secrets in one batch via the existing helper."""
    if dry_run:
        for k, v in secrets.items():
            print(f"  [DRY-RUN] would upsert {k}={v}")
        return
    upsert_path = Path(__file__).resolve().parents[3] / "scripts" / "infisical_upsert_secret.py"
    if not upsert_path.exists():
        raise RuntimeError(f"Helper not found: {upsert_path}")
    args = [sys.executable, str(upsert_path), "--environment=production"]
    for k, v in secrets.items():
        args.extend(["--secret", f"{k}={v}"])
    import subprocess
    result = subprocess.run(args, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"infisical_upsert_secret.py exited {result.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Show what would be upserted without writing.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    initialize_runtime_secrets(profile="local_workstation")

    print("🔑 Fetching YouTube OAuth access token...")
    access_token = _get_access_token()

    print("📋 Listing your YouTube playlists...")
    playlists = _list_my_playlists(access_token)
    print(f"   Found {len(playlists)} playlist(s) on your channel.")

    matches = _match_digest_playlists(playlists)

    print("\n🔍 Day-Digest playlist matches:")
    missing: list[str] = []
    secrets_to_upsert: dict[str, str] = {}
    for day in DAYS:
        if day in matches:
            pl_id = matches[day]
            secrets_to_upsert[f"{day}_YT_PLAYLIST"] = pl_id
            print(f"   ✓ {day:<10} → {pl_id}")
        else:
            missing.append(day)
            print(f"   ✗ {day:<10} → NOT FOUND (no playlist named '{day.title()} Digest')")

    if missing:
        print(f"\n❌ Missing {len(missing)} day(s): {', '.join(missing)}")
        print("   Create playlists named exactly '<Day> Digest' (case-insensitive) and re-run.")
        return 1

    print(f"\n✏️  Upserting {len(secrets_to_upsert)} secrets to Infisical production...")
    _upsert_to_infisical(secrets_to_upsert, dry_run=args.dry_run)

    if args.dry_run:
        print("\n🟡 Dry-run: nothing was written.")
    else:
        print("\n✅ Done. The youtube_daily_digest cron will pick these up on its next run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
