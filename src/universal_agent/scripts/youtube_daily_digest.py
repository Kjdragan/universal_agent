#!/usr/bin/env python3
"""Daily YouTube Digest Engine

This script runs autonomously on a schedule (via UA Cron Service). It:
1. Selects today's dedicated playlist (e.g. "Monday Digest" on Mondays).
2. Extracts transcripts using residential proxies (with graceful fallback).
3. Synthesizes a compressed retelling + meta-analysis via Gemini (Vertex AI).
4. Saves the markdown artifact to the daily_digests workspace.
5. Emits the digest as a CSI record so it appears in the CSI Feed dashboard
   and can be processed by the proactive signal pipeline.
6. Deletes processed videos from the playlist (clean inbox pattern).

Playlist IDs are stored in Infisical as <DAY>_YT_PLAYLIST:
  MONDAY_YT_PLAYLIST, TUESDAY_YT_PLAYLIST, ..., SUNDAY_YT_PLAYLIST

# TODO(proactive-signal): The daily digest currently produces a single
# markdown document. The existing CSI batch brief pipeline will sweep it
# for proactive signal candidates. To improve signal extraction, consider
# having the Gemini synthesis prompt output structured follow-up
# recommendations (e.g. explicit JSON action blocks alongside the
# markdown) that the batch brief can parse more deterministically.
# This would make Tutorial Pipeline Triggers and cross-video themes
# more reliably surfaced as proactive cards.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Fix python path for local execution if needed
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from google import genai
from google.genai import types

from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services.youtube_playlist_manager import (
    get_playlist_items,
    remove_playlist_item,
    YouTubeAPIError,
    YouTubeOAuthError,
)
from universal_agent.youtube_ingest import ingest_youtube_transcript

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]

SYNTHESIS_PROMPT = """You are an expert technical researcher and knowledge synthesizer.
You are given the transcripts and metadata of several YouTube videos that the user queued up to watch today.
Instead of watching them, the user relies on you to provide a "Compressed Retelling" and "Daily Digest".

For each video, provide:
1. A concise, dense summary of the core thesis and key facts.
2. Any actionable advice or technical insights.
3. A priority ranking (e.g., High/Medium/Low Value) based on the depth of information.

Finally, provide a "Meta-Synthesis" section at the top that identifies any cross-video themes,
learning insights, or neglected opportunities across the entire playlist.

If a video contains an excellent technical tutorial that the user should definitely try out,
call it out explicitly as a "TUTORIAL PIPELINE TRIGGER" candidate.

Here are the videos:
"""


# ---------------------------------------------------------------------------
# CSI Digest emission — write directly to the gateway's CSI SQLite DB
# ---------------------------------------------------------------------------

def _emit_csi_digest(
    *,
    day_name: str,
    date_str: str,
    digest_content: str,
    video_count: int,
    video_titles: list[str],
) -> bool:
    """Write the daily digest as a CSI digest record for dashboard visibility.

    The digest is stored in the same SQLite DB used by the UA gateway's CSI
    Feed, so it shows up alongside Reddit/Threads/YouTube RSS digests and
    can be processed by the batch brief / proactive signal pipeline.
    """
    # Locate the CSI digests DB — same path the gateway uses
    workspaces_dir = Path(os.getenv("UA_WORKSPACE_DIR") or Path.cwd()) / "workspaces"
    db_path = workspaces_dir / ".csi_digests.db"

    if not db_path.exists():
        logger.warning("CSI digest DB not found at %s — digest will not appear in CSI Feed", db_path)
        return False

    digest_id = str(uuid.uuid4())
    event_id = f"yt_daily_digest_{date_str}_{day_name.lower()}"
    title = f"Daily YouTube Digest: {day_name.title()}, {date_str} ({video_count} videos)"
    summary_lines = [f"Processed {video_count} videos from the {day_name.title()} Digest playlist."]
    if video_titles:
        summary_lines.append("Videos: " + " · ".join(video_titles[:5]))
        if len(video_titles) > 5:
            summary_lines.append(f"  ...and {len(video_titles) - 5} more")
    summary = " ".join(summary_lines)[:500]

    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.execute(
            "INSERT OR REPLACE INTO csi_digests "
            "(id, event_id, source, event_type, title, summary, full_report_md, source_types, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                digest_id,
                event_id,
                "youtube_daily_digest",
                "youtube_daily_digest",
                title,
                summary,
                digest_content,
                json.dumps(["youtube"]),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        logger.info("Emitted CSI digest record: %s", digest_id)
        return True
    except Exception as exc:
        logger.error("Failed to emit CSI digest: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_daily_digest(dry_run: bool = False):
    initialize_runtime_secrets()

    day_name = DAYS[datetime.now().weekday()]
    playlist_id_var = f"{day_name}_YT_PLAYLIST"
    playlist_id = os.getenv(playlist_id_var)

    if not playlist_id:
        logger.warning("No playlist configured for today: %s (%s is not set)", day_name, playlist_id_var)
        return

    logger.info("Starting Daily Digest for %s (Playlist: %s)", day_name, playlist_id)

    try:
        items = get_playlist_items(playlist_id)
    except (YouTubeAPIError, YouTubeOAuthError) as e:
        logger.error("Failed to fetch playlist items: %s", e)
        return

    if not items:
        logger.info("Playlist is empty. Nothing to process today.")
        return

    logger.info("Found %d videos in the playlist.", len(items))

    transcripts: list[str] = []
    processed_items: list[dict] = []
    video_titles: list[str] = []

    for item in items:
        video_id = item["video_id"]
        title = item["title"]
        logger.info("Ingesting: %s (%s)", title, video_id)
        video_titles.append(title)

        # Strategy: try proxy first (VPS path), then no-proxy, then metadata-only
        result = None
        for attempt_proxy in [True, False]:
            try:
                result = ingest_youtube_transcript(
                    video_url=None,
                    video_id=video_id,
                    require_proxy=attempt_proxy,
                )
                if result.get("ok"):
                    break
                # If proxy-specific error, try without proxy
                detail = str(result.get("detail", ""))
                if attempt_proxy and ("407" in detail or "NO_USER" in detail or "proxy" in detail.lower()):
                    logger.info("Proxy failed for %s, retrying without proxy...", video_id)
                    continue
                break  # non-proxy error, don't retry
            except Exception as exc:
                logger.warning("Ingestion exception for %s (proxy=%s): %s", video_id, attempt_proxy, exc)
                if attempt_proxy:
                    continue
                result = {"ok": False, "error": str(exc)}

        if result and result.get("ok"):
            text = result.get("transcript_text", "")
            if len(text) > 50000:
                text = text[:50000] + "... [TRUNCATED]"

            transcripts.append(f"Title: {title}\nVideo ID: {video_id}\nTranscript:\n{text}\n")
            processed_items.append(item)
        else:
            error_detail = result.get("detail", result.get("error", "unknown")) if result else "unknown"
            logger.warning("Failed to ingest %s: %s", video_id, error_detail)

            # Metadata-only fallback: use title for synthesis (playlist API doesn't return description)
            fallback_text = f"[Metadata-only — transcript unavailable]\n\nTitle: {title}"
            transcripts.append(f"Title: {title}\nVideo ID: {video_id}\n{fallback_text}\n")
            processed_items.append(item)
            logger.info("Using metadata-only fallback for %s", video_id)

    if not transcripts:
        logger.info("No transcripts could be extracted. Exiting.")
        return

    logger.info("Generating Meta-Synthesis with Gemini (Vertex AI)...")

    # Always use Vertex AI — works on both VPS (service account) and desktop (ADC)
    client = genai.Client(vertexai=True)

    full_prompt = SYNTHESIS_PROMPT + "\n\n---\n\n".join(transcripts)

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=full_prompt,
            config=types.GenerateContentConfig(
                temperature=0.4,
            ),
        )
        digest_content = response.text
    except Exception as e:
        logger.error("Failed to generate content with Gemini: %s", e)
        return

    # Save Artifact to the persistent daily_digests workspace
    date_str = datetime.now().strftime("%Y-%m-%d")
    workspace_dir = Path(os.getenv("UA_WORKSPACE_DIR") or Path.cwd()) / "workspaces"
    artifacts_dir = workspace_dir / "daily_digests"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    artifact_path = artifacts_dir / f"{date_str}_{day_name}_Digest.md"
    full_content = f"# Daily YouTube Digest: {day_name.title()}, {date_str}\n\n{digest_content}"

    with open(artifact_path, "w") as f:
        f.write(full_content)

    logger.info("Daily Digest saved to: %s", artifact_path)

    # Emit as a CSI digest record for dashboard visibility + proactive signal pipeline
    _emit_csi_digest(
        day_name=day_name,
        date_str=date_str,
        digest_content=full_content,
        video_count=len(processed_items),
        video_titles=video_titles,
    )

    if dry_run:
        logger.info("DRY RUN enabled. Skipping physical deletion of videos.")
        return

    logger.info("Starting physical deletion of processed videos...")
    for item in processed_items:
        try:
            success = remove_playlist_item(item["playlist_item_id"])
            if success:
                logger.info("Deleted %s from playlist.", item["video_id"])
        except Exception as e:
            logger.error("Failed to delete item %s: %s", item["playlist_item_id"], e)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the daily YouTube digest.")
    parser.add_argument("--dry-run", action="store_true", help="Do not delete videos from the playlist.")
    parser.add_argument("--day", type=str, default=None,
                        help="Override day of week (e.g., 'MONDAY'). Uses current day if not set.")
    args = parser.parse_args()

    if args.day:
        day_upper = args.day.upper()
        if day_upper in DAYS:
            # Monkey-patch the weekday() to return the desired day index
            _original_now = datetime.now
            _day_idx = DAYS.index(day_upper)

            class _FakeDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    real = _original_now(tz)
                    # Shift to the desired weekday
                    delta = _day_idx - real.weekday()
                    return real.replace(day=real.day) if delta == 0 else real

            # Simple approach: just set the env var
            os.environ[f"{day_upper}_YT_PLAYLIST"] = os.getenv(f"{day_upper}_YT_PLAYLIST", "")
            logger.info("Day override: %s", day_upper)

    process_daily_digest(dry_run=args.dry_run)
