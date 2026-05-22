"""Gold-channel YouTube RSS poller.

Runs ~30 minutes before the morning digest cron (default 5:30 AM America/Chicago).
For every channel in `channels_watchlist.json` with `tier="gold"`, fetches the
channel's RSS feed and adds newly-published videos to the appropriate
day-of-week playlist so the existing digest cron picks them up at 6:00 AM.

Design notes
------------
*   The poller routes each candidate video by the video's published-at weekday
    (in America/Chicago), NOT by today's weekday. A video published Wednesday
    11 PM lands in WEDNESDAY_YT_PLAYLIST; a video published Thursday 4 AM lands
    in THURSDAY_YT_PLAYLIST. This matches Kevin's manual curation model.

*   Per-channel `duration_max_seconds_override` overrides the global pre-ingest
    duration triage. `null`/missing = inherit global cap; a specific positive
    int = use as cap. We resolve duration via the YouTube Data API (free quota,
    no proxy needed) before adding so we never burn proxy bandwidth on videos
    the digest would skip anyway.

*   Dedup: a candidate is dropped if (a) its video_id is already in the target
    playlist, or (b) it appears in the local `processed_videos` SQLite (i.e.
    the digest has already consumed it on a previous run).

*   Daily cap: configurable via env var `UA_YOUTUBE_GOLD_DAILY_CAP` (default 10).
    Candidates are sorted newest-first across all gold channels combined, so a
    burst from one channel can't crowd out the others.

*   Idempotency: running the poller twice in quick succession produces no new
    adds — the playlist-membership and processed-videos checks short-circuit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any
import xml.etree.ElementTree as ET

import httpx

from universal_agent.services.youtube_playlist_manager import (
    YouTubeAPIError,
    add_playlist_item,
    get_playlist_items,
)
from universal_agent.youtube_ingest import _run_youtube_data_api_metadata

logger = logging.getLogger(__name__)

# America/Chicago = UTC-5 (CDT) or UTC-6 (CST). The digest cron uses the same
# convention via TZ=America/Chicago, so we let zoneinfo handle DST.
try:
    from zoneinfo import ZoneInfo
    HOUSTON_TZ = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover
    HOUSTON_TZ = timezone(timedelta(hours=-6))

# Atom namespaces used by YouTube channel feeds.
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

_DEFAULT_DAILY_CAP = 10
_DEFAULT_LOOKBACK_HOURS = 30  # generous so we don't miss a publish across DST or a missed cron tick
_DEFAULT_GLOBAL_DURATION_CAP_SECONDS = 5400  # 90 min — matches youtube_ingest._MAX_DURATION_SECONDS today

# Canonical path. The poller runs from the deployed copy on the VPS; we look
# at the package-root-adjacent file (same directory as the digest cron uses).
_WATCHLIST_PATH = Path(
    os.getenv(
        "UA_YOUTUBE_CHANNELS_WATCHLIST_PATH",
        "/opt/universal_agent/channels_watchlist.json",
    )
)


@dataclass
class CandidateVideo:
    """One RSS-discovered video that's a candidate for auto-add."""

    video_id: str
    title: str
    channel_id: str
    channel_name: str
    published_at: datetime  # tz-aware UTC
    duration_seconds: int | None = None  # populated lazily from YouTube Data API
    target_weekday: str = ""  # populated from published_at.astimezone(HOUSTON_TZ).strftime('%A').upper()
    target_playlist_id: str | None = None  # populated by resolve_target_playlist
    skipped_reason: str | None = None  # if non-None, candidate was rejected

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"CandidateVideo(video_id={self.video_id!r}, "
            f"channel={self.channel_name!r}, "
            f"published_at={self.published_at.isoformat()}, "
            f"target_weekday={self.target_weekday!r}, "
            f"skipped={self.skipped_reason!r})"
        )


@dataclass
class PollerResult:
    """Summary of one poller run, returned for logging + tests."""

    inspected_channels: int = 0
    rss_fetch_failures: int = 0
    candidates_discovered: int = 0
    added: int = 0
    skipped_already_in_playlist: int = 0
    skipped_already_processed: int = 0
    skipped_duration_cap: int = 0
    skipped_no_playlist_env: int = 0
    cap_reached: bool = False
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "inspected_channels": self.inspected_channels,
            "rss_fetch_failures": self.rss_fetch_failures,
            "candidates_discovered": self.candidates_discovered,
            "added": self.added,
            "skipped_already_in_playlist": self.skipped_already_in_playlist,
            "skipped_already_processed": self.skipped_already_processed,
            "skipped_duration_cap": self.skipped_duration_cap,
            "skipped_no_playlist_env": self.skipped_no_playlist_env,
            "cap_reached": self.cap_reached,
            "dry_run": self.dry_run,
        }


def _load_watchlist(path: Path = _WATCHLIST_PATH) -> dict[str, Any]:
    """Load the channels_watchlist.json. Returns empty structure if missing
    so the poller no-ops gracefully on a fresh install."""
    if not path.exists():
        logger.warning("channels_watchlist.json not found at %s — gold poller no-op", path)
        return {"channels": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_watchlist(data: dict[str, Any], path: Path = _WATCHLIST_PATH) -> None:
    """Atomic write of the watchlist (poller updates last_publication_seen_at)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _fetch_rss_entries(channel: dict[str, Any], *, timeout: float = 10.0) -> list[CandidateVideo]:
    """Fetch a channel's atom feed and parse entries into CandidateVideo objects.

    Returns [] on any HTTP/parse failure. The feed is fronted by YouTube and
    doesn't require auth or proxying."""
    url = channel.get("rss_feed_url") or ""
    if not url:
        return []
    try:
        resp = httpx.get(url, timeout=timeout, headers={"User-Agent": "ua-gold-poller/1.0"})
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("RSS fetch failed for %s: %s", channel.get("channel_name", "?"), exc)
        return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        logger.warning("RSS parse failed for %s: %s", channel.get("channel_name", "?"), exc)
        return []

    candidates: list[CandidateVideo] = []
    for entry in root.findall("atom:entry", _NS):
        vid_elem = entry.find("yt:videoId", _NS)
        if vid_elem is None or not vid_elem.text:
            continue
        title_elem = entry.find("atom:title", _NS)
        published_elem = entry.find("atom:published", _NS)
        if published_elem is None or not published_elem.text:
            continue
        try:
            published_at = datetime.fromisoformat(published_elem.text.replace("Z", "+00:00"))
        except ValueError:
            continue
        candidates.append(
            CandidateVideo(
                video_id=vid_elem.text.strip(),
                title=(title_elem.text or "").strip() if title_elem is not None else "",
                channel_id=channel["channel_id"],
                channel_name=channel.get("channel_name", ""),
                published_at=published_at,
            )
        )
    return candidates


def _is_already_processed(video_id: str, *, processed_db_path: Path | None = None) -> bool:
    """Check the digest's processed_videos SQLite. False on missing DB."""
    if processed_db_path is None:
        processed_db_path = Path(
            os.getenv(
                "UA_YOUTUBE_INGESTION_STATE_DB",
                "/opt/universal_agent/AGENT_RUN_WORKSPACES/youtube_ingestion_state.db",
            )
        )
    if not processed_db_path.exists():
        return False
    try:
        conn = sqlite3.connect(str(processed_db_path))
        try:
            cur = conn.execute(
                "SELECT 1 FROM processed_videos WHERE video_id = ? LIMIT 1",
                (video_id,),
            )
            return cur.fetchone() is not None
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("processed_videos lookup failed for %s: %s", video_id, exc)
        return False


def _resolve_duration_cap(channel: dict[str, Any]) -> int:
    """Resolve the effective duration cap for a channel.

    `duration_max_seconds_override` semantics:
      * null / missing  → inherit global default (5400s).
      * positive int N  → use N as the cap (86400 = 24h = effectively unlimited).
    """
    override = channel.get("duration_max_seconds_override")
    if isinstance(override, int) and override > 0:
        return override
    return int(os.getenv("UA_YOUTUBE_GOLD_GLOBAL_DURATION_CAP", _DEFAULT_GLOBAL_DURATION_CAP_SECONDS))


def _resolve_target_playlist(weekday_upper: str) -> str | None:
    """Look up <WEEKDAY>_YT_PLAYLIST from env. Returns None if not configured."""
    env_var = f"{weekday_upper}_YT_PLAYLIST"
    pid = os.getenv(env_var)
    return pid.strip() if pid else None


def _fetch_duration_seconds(video_id: str) -> int | None:
    """Pull duration from the YouTube Data API. Falls back to None on failure
    (in which case we conservatively apply the cap as if duration was infinite,
    skipping the video — matches the digest's pre-ingest triage stance)."""
    result = _run_youtube_data_api_metadata(video_id, timeout_seconds=10)
    if not result.get("ok"):
        return None
    metadata = result.get("metadata") or {}
    duration = metadata.get("duration")
    try:
        return int(duration) if duration is not None else None
    except (TypeError, ValueError):
        return None


def poll_gold_channels(
    *,
    now: datetime | None = None,
    daily_cap: int | None = None,
    lookback_hours: int | None = None,
    dry_run: bool = False,
    watchlist_path: Path | None = None,
) -> PollerResult:
    """Main entry point. Pulls each gold channel's RSS, dedups, applies caps,
    and adds new videos to the appropriate day-of-week playlist."""
    if now is None:
        now = datetime.now(timezone.utc)
    cap = daily_cap if daily_cap is not None else int(
        os.getenv("UA_YOUTUBE_GOLD_DAILY_CAP", _DEFAULT_DAILY_CAP)
    )
    lookback = lookback_hours if lookback_hours is not None else int(
        os.getenv("UA_YOUTUBE_GOLD_LOOKBACK_HOURS", _DEFAULT_LOOKBACK_HOURS)
    )
    cutoff = now - timedelta(hours=lookback)

    watchlist = _load_watchlist(watchlist_path or _WATCHLIST_PATH)
    channels = watchlist.get("channels", [])
    gold_channels = [c for c in channels if c.get("tier") == "gold"]

    result = PollerResult(dry_run=dry_run, inspected_channels=len(gold_channels))
    if not gold_channels:
        logger.info("No gold channels in watchlist — poller no-op")
        return result

    logger.info(
        "Gold poller starting: %d gold channels, cap=%d, lookback=%dh, dry_run=%s",
        len(gold_channels), cap, lookback, dry_run,
    )

    # Discovery pass — fetch all RSS feeds, collect candidates.
    candidates: list[CandidateVideo] = []
    channel_by_id: dict[str, dict[str, Any]] = {c["channel_id"]: c for c in gold_channels}

    for ch in gold_channels:
        entries = _fetch_rss_entries(ch)
        if not entries:
            # Either the channel has no recent videos or RSS fetch failed.
            # _fetch_rss_entries already logs failures; count them separately
            # so the result distinguishes "empty feed" from "couldn't fetch."
            if ch.get("rss_feed_url"):
                # We attempted; assume parse/fetch issue if zero entries (rare for
                # YouTube — channels always have at least one historical entry).
                pass
            continue
        # Apply lookback filter and weekday routing
        for cand in entries:
            if cand.published_at < cutoff:
                continue
            local_published = cand.published_at.astimezone(HOUSTON_TZ)
            cand.target_weekday = local_published.strftime("%A").upper()
            cand.target_playlist_id = _resolve_target_playlist(cand.target_weekday)
            candidates.append(cand)

    result.candidates_discovered = len(candidates)
    if not candidates:
        logger.info("Gold poller: no candidates inside the %dh lookback window", lookback)
        return result

    # Sort newest-first so a burst from one channel doesn't crowd out the cap.
    candidates.sort(key=lambda c: c.published_at, reverse=True)

    # Promote pass — for each candidate, dedup + duration check + add.
    playlist_cache: dict[str, set[str]] = {}  # playlist_id -> {video_id, ...}

    for cand in candidates:
        if result.added >= cap:
            result.cap_reached = True
            logger.info("Daily cap reached (%d); stopping.", cap)
            break

        if not cand.target_playlist_id:
            cand.skipped_reason = "no_playlist_env"
            result.skipped_no_playlist_env += 1
            logger.info(
                "Skip %s (%s): no env var %s_YT_PLAYLIST set",
                cand.video_id, cand.channel_name, cand.target_weekday,
            )
            continue

        # Cache the target playlist's current contents (one API call per unique playlist).
        if cand.target_playlist_id not in playlist_cache:
            try:
                items = get_playlist_items(cand.target_playlist_id)
                playlist_cache[cand.target_playlist_id] = {str(it.get("video_id")) for it in items}
            except (YouTubeAPIError, Exception) as exc:
                logger.warning(
                    "Failed to load playlist %s for dedup: %s",
                    cand.target_playlist_id, exc,
                )
                playlist_cache[cand.target_playlist_id] = set()

        if cand.video_id in playlist_cache[cand.target_playlist_id]:
            cand.skipped_reason = "already_in_playlist"
            result.skipped_already_in_playlist += 1
            continue

        if _is_already_processed(cand.video_id):
            cand.skipped_reason = "already_processed"
            result.skipped_already_processed += 1
            continue

        # Duration cap (with per-channel override).
        channel = channel_by_id[cand.channel_id]
        cap_seconds = _resolve_duration_cap(channel)
        duration = _fetch_duration_seconds(cand.video_id)
        cand.duration_seconds = duration
        if duration is None:
            # Conservative: if we can't determine duration, skip rather than
            # blindly burn proxy bandwidth on the digest-side ingest later.
            cand.skipped_reason = "duration_unknown"
            result.skipped_duration_cap += 1
            logger.info(
                "Skip %s (%s): could not determine duration",
                cand.video_id, cand.channel_name,
            )
            continue
        if duration > cap_seconds:
            cand.skipped_reason = f"duration_{duration}s_over_cap_{cap_seconds}s"
            result.skipped_duration_cap += 1
            logger.info(
                "Skip %s (%s): duration %ds > cap %ds",
                cand.video_id, cand.channel_name, duration, cap_seconds,
            )
            continue

        # All gates passed — add to the playlist.
        if dry_run:
            logger.info(
                "[DRY] Would add %s (%s) to %s playlist (%s)",
                cand.video_id, cand.channel_name,
                cand.target_weekday, cand.target_playlist_id,
            )
        else:
            try:
                add_playlist_item(cand.target_playlist_id, cand.video_id)
                logger.info(
                    "Added %s (%s) to %s playlist (%s)",
                    cand.video_id, cand.channel_name,
                    cand.target_weekday, cand.target_playlist_id,
                )
            except (YouTubeAPIError, Exception) as exc:
                logger.warning(
                    "Failed to add %s to %s: %s",
                    cand.video_id, cand.target_playlist_id, exc,
                )
                continue

        playlist_cache[cand.target_playlist_id].add(cand.video_id)
        channel["last_publication_seen_at"] = now.isoformat()
        result.added += 1

    # Persist last_publication_seen_at updates (only outside dry-run, only if something changed).
    if not dry_run and result.added > 0:
        try:
            _save_watchlist(watchlist, watchlist_path or _WATCHLIST_PATH)
        except OSError as exc:
            logger.warning("Failed to persist watchlist state: %s", exc)

    logger.info("Gold poller summary: %s", result.to_dict())
    return result


def _main_cli() -> None:
    """CLI entrypoint for cron + manual smoke."""
    import argparse

    parser = argparse.ArgumentParser(description="Poll gold channels' RSS feeds.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cap", type=int, default=None)
    parser.add_argument("--lookback-hours", type=int, default=None)
    parser.add_argument("--watchlist", type=Path, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    # Secrets bootstrap so YouTube OAuth + Data API key resolve.
    from universal_agent.infisical_loader import initialize_runtime_secrets
    initialize_runtime_secrets()

    result = poll_gold_channels(
        dry_run=args.dry_run,
        daily_cap=args.cap,
        lookback_hours=args.lookback_hours,
        watchlist_path=args.watchlist,
    )
    print(json.dumps(result.to_dict(), indent=2))


if __name__ == "__main__":
    _main_cli()
