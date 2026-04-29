"""YouTube channel RSS adapter."""

from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from csi_ingester.adapters.base import RawEvent, SourceAdapter
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.store.source_manager import (
    get_active_youtube_channels,
    seed_youtube_channels,
)

ATOM_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}
logger = logging.getLogger(__name__)


class YouTubeChannelRSSAdapter(SourceAdapter):
    """Detect channel uploads from YouTube RSS feeds."""

    def __init__(self, config: dict):
        self.config = config
        self._seen_by_channel: dict[str, set[str]] = {}
        self._etag_by_channel: dict[str, str] = {}
        self._modified_by_channel: dict[str, str] = {}
        self._seeded_by_channel: dict[str, bool] = {}
        self._seed_on_first_run = bool(config.get("seed_on_first_run", True))
        self._max_seen_cache = max(50, int(config.get("max_seen_cache_per_channel", 1000)))
        self._watchlist_file = str(config.get("watchlist_file") or "").strip()
        self._watchlist_fallback_file = Path(__file__).resolve().parents[2] / "channels_watchlist.json"
        self._watchlist_file_mtime: float | None = None
        self._watchlist_file_channels: list[dict[str, str]] = []
        self._db_conn: sqlite3.Connection | None = None
        self._db_seeded: bool = False
        self._load_state_fn = lambda source_key: None
        self._save_state_fn = lambda source_key, state: None
        # ── Time-aware schedule ──────────────────────────────────────
        self._schedule_tz_name = str(config.get("schedule_timezone") or "").strip()
        self._schedule_fetch_hours: list[int] = []
        raw_hours = config.get("schedule_fetch_hours")
        if isinstance(raw_hours, list):
            self._schedule_fetch_hours = [int(h) for h in raw_hours if isinstance(h, (int, float))]
        self._schedule_min_interval = max(0, int(config.get("schedule_min_interval_seconds", 0)))
        self._last_fetch_epoch: float = 0.0

    def set_state_backend(self, load_state_fn, save_state_fn) -> None:
        self._load_state_fn = load_state_fn
        self._save_state_fn = save_state_fn

    def set_db_connection(self, conn: sqlite3.Connection) -> None:
        """Set the DB connection for source management queries."""
        self._db_conn = conn

    async def fetch_events(self) -> list[RawEvent]:
        # ── Time-aware schedule gate ─────────────────────────────────
        # If schedule_fetch_hours is configured, only actually fetch RSS
        # when the current hour (in the configured timezone) is in the list
        # AND enough time has elapsed since the last fetch.
        if self._schedule_fetch_hours:
            import time
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(self._schedule_tz_name or "America/Chicago")
            except Exception:
                # Fallback: assume UTC-5 (CDT) offset manually
                from datetime import timedelta
                tz = timezone(timedelta(hours=-5))
            now_ct = datetime.now(tz)
            current_hour = now_ct.hour
            now_epoch = time.time()
            if current_hour not in self._schedule_fetch_hours:
                logger.debug(
                    "Schedule gate: skipping RSS fetch — CT hour %d not in allowed hours %s",
                    current_hour, self._schedule_fetch_hours,
                )
                return []
            if self._schedule_min_interval and self._last_fetch_epoch:
                elapsed = now_epoch - self._last_fetch_epoch
                if elapsed < self._schedule_min_interval:
                    logger.debug(
                        "Schedule gate: skipping RSS fetch — only %.0fs since last fetch (min %ds)",
                        elapsed, self._schedule_min_interval,
                    )
                    return []
            logger.info(
                "Schedule gate: proceeding with RSS fetch — CT hour %d, last fetch %.0fs ago",
                current_hour, now_epoch - self._last_fetch_epoch if self._last_fetch_epoch else 0,
            )

        watchlist = self._resolve_watchlist()
        timeout_seconds = max(5, int(self.config.get("timeout_seconds", 20)))
        max_concurrency = max(1, int(self.config.get("max_concurrency", 20)))
        events: list[RawEvent] = []
        # Route through residential proxy when configured to avoid VPS IP blocks.
        # Set CSI_RSS_PROXY_URL=http://user:pass@host:port in csi-ingester.env
        proxy_url: str | None = (
            str(self.config.get("proxy_url") or os.getenv("CSI_RSS_PROXY_URL") or "").strip() or None
        )
        client_kwargs: dict[str, Any] = {"timeout": timeout_seconds}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url

        sem = asyncio.Semaphore(max_concurrency)
        failures = 0
        total = len(watchlist)

        async def _fetch_one(item: dict[str, str]) -> tuple[str, str, list[dict[str, Any]]]:
            channel_id = str(item.get("channel_id") or "").strip()
            channel_name = str(item.get("channel_name") or "").strip()
            if not channel_id:
                return ("", "", [])
            self._hydrate_channel_state(channel_id)
            async with sem:
                try:
                    entries = await self._fetch_channel_entries(client, channel_id=channel_id)
                except Exception as exc:
                    logger.warning("RSS fetch channel failed channel_id=%s error=%s", channel_id, exc)
                    return (channel_id, channel_name, [])
            return (channel_id, channel_name, entries)

        async with httpx.AsyncClient(**client_kwargs) as client:
            tasks = [_fetch_one(item) for item in watchlist]
            results = await asyncio.gather(*tasks)

        for channel_id, channel_name, entries in results:
            if not channel_id:
                continue
            if not entries:
                self._persist_channel_state(channel_id)
                failures += 1
                # Early-exit circuit breaker: abort cycle if >50% channels fail
                if total > 4 and failures / total > 0.5:
                    logger.error(
                        "RSS circuit breaker: %.0f%% failures (%d/%d), aborting cycle",
                        failures / total * 100,
                        failures,
                        total,
                    )
                    return events
                continue
            if channel_name:
                for entry in entries:
                    entry.setdefault("channel_name", channel_name)
            seen = self._seen_by_channel.setdefault(channel_id, set())
            current_ids = [entry["video_id"] for entry in entries if entry.get("video_id")]
            seeded = self._seeded_by_channel.get(channel_id, False)
            if not seen and self._seed_on_first_run and not seeded:
                seen.update(current_ids)
                self._seeded_by_channel[channel_id] = True
                self._persist_channel_state(channel_id)
                continue
            new_entries = [entry for entry in entries if entry.get("video_id") and entry["video_id"] not in seen]
            for entry in reversed(new_entries):
                events.append(
                    RawEvent(
                        source="youtube_channel_rss",
                        event_type="channel_new_upload",
                        payload=entry,
                        occurred_at=str(entry.get("occurred_at") or _iso_now()),
                    )
                )
                seen.add(entry["video_id"])
            self._seeded_by_channel[channel_id] = True
            self._seen_by_channel[channel_id] = set(current_ids[: self._max_seen_cache])
            self._persist_channel_state(channel_id)
        # Update last-fetch timestamp for schedule gating
        import time
        self._last_fetch_epoch = time.time()
        return events

    def _resolve_watchlist(self) -> list[dict[str, str]]:
        # ── Try DB-backed source list first ──
        if self._db_conn is not None:
            try:
                return self._resolve_watchlist_from_db()
            except Exception as exc:
                logger.warning("DB watchlist query failed, falling back to JSON: %s", exc)

        # ── Fallback: legacy JSON-based resolution ──
        configured = list(self.config.get("watchlist") or [])
        merged: list[dict[str, str]] = []
        seen: set[str] = set()

        def _push_item(item: dict[str, Any]) -> None:
            channel_id = str(item.get("channel_id") or "").strip()
            if not channel_id or channel_id in seen:
                return
            seen.add(channel_id)
            channel_name = str(item.get("channel_name") or "").strip()
            row = {"channel_id": channel_id}
            if channel_name:
                row["channel_name"] = channel_name
            merged.append(row)

        for item in configured:
            if isinstance(item, dict):
                _push_item(item)

        for item in self._load_watchlist_file_channels():
            _push_item(item)

        if not merged:
            logger.warning(
                "RSS watchlist resolved to zero channels configured_inline=%d configured_file=%s fallback_file=%s",
                len(configured),
                self._watchlist_file or "(unset)",
                self._watchlist_fallback_file,
            )

        return merged

    def _resolve_watchlist_from_db(self) -> list[dict[str, str]]:
        """Resolve watchlist from SQLite source tables.

        Seeds from JSON on first call, then returns active channels.
        """
        assert self._db_conn is not None
        if not self._db_seeded:
            seed_path = Path(self._watchlist_file).expanduser() if self._watchlist_file else None
            if seed_path and not seed_path.exists():
                seed_path = self._watchlist_fallback_file
            if seed_path and seed_path.exists():
                count = seed_youtube_channels(self._db_conn, seed_path)
                logger.info("YouTube channels seeded from JSON: %d", count)
            self._db_seeded = True

        channels = get_active_youtube_channels(self._db_conn)
        logger.info("YouTube watchlist from DB: %d active channels", len(channels))
        return [
            {"channel_id": ch["channel_id"], "channel_name": ch.get("channel_name", "")}
            for ch in channels
        ]

    def _load_watchlist_file_channels(self) -> list[dict[str, str]]:
        if not self._watchlist_file:
            return []
        path = Path(self._watchlist_file).expanduser()
        if not path.exists():
            fallback_path = self._watchlist_fallback_file
            if fallback_path.exists() and fallback_path != path:
                logger.warning(
                    "RSS watchlist file missing path=%s; using fallback path=%s",
                    path,
                    fallback_path,
                )
                path = fallback_path
            elif self._watchlist_file_channels:
                logger.warning("RSS watchlist file missing path=%s; keeping previous cached list", path)
                return self._watchlist_file_channels
            else:
                logger.warning("RSS watchlist file missing path=%s and no cached channels available", path)
                return self._watchlist_file_channels

        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            logger.warning("RSS watchlist file stat failed path=%s error=%s", path, exc)
            return self._watchlist_file_channels

        if self._watchlist_file_mtime is not None and mtime == self._watchlist_file_mtime:
            return self._watchlist_file_channels

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("RSS watchlist file parse failed path=%s error=%s", path, exc)
            return self._watchlist_file_channels

        channels: list[dict[str, str]] = []
        if isinstance(payload, dict):
            raw_channels = payload.get("channels")
            if isinstance(raw_channels, list):
                for row in raw_channels:
                    if isinstance(row, dict):
                        channel_id = str(row.get("channel_id") or "").strip()
                        if channel_id:
                            channel_name = str(row.get("channel_name") or "").strip()
                            item = {"channel_id": channel_id}
                            if channel_name:
                                item["channel_name"] = channel_name
                            channels.append(item)
        elif isinstance(payload, list):
            for row in payload:
                if isinstance(row, dict):
                    channel_id = str(row.get("channel_id") or "").strip()
                    if channel_id:
                        channel_name = str(row.get("channel_name") or "").strip()
                        item = {"channel_id": channel_id}
                        if channel_name:
                            item["channel_name"] = channel_name
                        channels.append(item)
                elif isinstance(row, str) and row.strip():
                    channels.append({"channel_id": row.strip()})

        self._watchlist_file_mtime = mtime
        self._watchlist_file_channels = channels
        logger.info("RSS watchlist loaded path=%s channels=%d", path, len(channels))
        return self._watchlist_file_channels

    async def _fetch_channel_entries(self, client: httpx.AsyncClient, *, channel_id: str) -> list[dict[str, Any]]:
        headers = {"Accept": "application/atom+xml,application/xml,text/xml"}
        etag = self._etag_by_channel.get(channel_id)
        if etag:
            headers["If-None-Match"] = etag
        modified = self._modified_by_channel.get(channel_id)
        if modified:
            headers["If-Modified-Since"] = modified
        try:
            response = await client.get(
                "https://www.youtube.com/feeds/videos.xml",
                params={"channel_id": channel_id},
                headers=headers,
            )
        except Exception as exc:
            logger.warning("RSS request failed channel_id=%s error=%s", channel_id, exc)
            return []
        if response.status_code == 304:
            return []
        if response.status_code >= 400:
            logger.warning("RSS request error channel_id=%s status=%s", channel_id, response.status_code)
            return []
        if "etag" in response.headers:
            self._etag_by_channel[channel_id] = response.headers["etag"]
        if "last-modified" in response.headers:
            self._modified_by_channel[channel_id] = response.headers["last-modified"]

        try:
            root = ET.fromstring(response.text)
        except Exception as exc:
            logger.warning("RSS parse failed channel_id=%s error=%s", channel_id, exc)
            return []
        entries: list[dict[str, Any]] = []
        for entry in root.findall("a:entry", ATOM_NS):
            video_id = _safe_text(entry.find("yt:videoId", ATOM_NS))
            if not video_id:
                continue
            link = entry.find("a:link", ATOM_NS)
            href = link.attrib.get("href", "").strip() if link is not None else ""
            if _is_youtube_short_url(href):
                continue
            published_at = _safe_text(entry.find("a:published", ATOM_NS)) or _iso_now()
            media_group = entry.find("media:group", ATOM_NS)
            thumbnail = media_group.find("media:thumbnail", ATOM_NS) if media_group is not None else None
            author = entry.find("a:author", ATOM_NS)
            entries.append(
                {
                    "video_id": video_id,
                    "channel_id": channel_id,
                    "url": href or f"https://www.youtube.com/watch?v={video_id}",
                    "title": _safe_text(entry.find("a:title", ATOM_NS)),
                    "description": _safe_text(media_group.find("media:description", ATOM_NS)) if media_group is not None else "",
                    "thumbnail_url": thumbnail.attrib.get("url", "").strip() if thumbnail is not None else "",
                    "media_title": _safe_text(media_group.find("media:title", ATOM_NS)) if media_group is not None else "",
                    "author_name": _safe_text(author.find("a:name", ATOM_NS)) if author is not None else "",
                    "author_uri": _safe_text(author.find("a:uri", ATOM_NS)) if author is not None else "",
                    "published_at": published_at,
                    "updated_at": _safe_text(entry.find("a:updated", ATOM_NS)),
                    "occurred_at": published_at,
                }
            )
        return entries

    def normalize(self, raw: RawEvent) -> CreatorSignalEvent:
        now = _iso_now()
        payload = raw.payload
        subject = {
            "platform": "youtube",
            "video_id": str(payload.get("video_id") or ""),
            "channel_id": str(payload.get("channel_id") or ""),
            "channel_name": str(payload.get("channel_name") or ""),
            "url": str(payload.get("url") or ""),
            "title": str(payload.get("title") or ""),
            "description": str(payload.get("description") or ""),
            "thumbnail_url": str(payload.get("thumbnail_url") or ""),
            "media_title": str(payload.get("media_title") or ""),
            "author_name": str(payload.get("author_name") or ""),
            "author_uri": str(payload.get("author_uri") or ""),
            "published_at": str(payload.get("published_at") or now),
            "updated_at": str(payload.get("updated_at") or ""),
        }
        event = CreatorSignalEvent(
            event_id=str(payload.get("event_id") or f"yt:rss:{subject['video_id']}:{int(datetime.now(timezone.utc).timestamp())}"),
            dedupe_key="",
            source="youtube_channel_rss",
            event_type="channel_new_upload",
            occurred_at=raw.occurred_at,
            received_at=now,
            subject=subject,
            routing={"pipeline": "creator_watchlist_handler", "priority": "standard", "tags": ["youtube", "watchlist"]},
            metadata={"source_adapter": "youtube_channel_rss_v1"},
        )
        event.dedupe_key = self.get_dedupe_key(event)
        return event

    def get_dedupe_key(self, event: CreatorSignalEvent) -> str:
        return f"youtube:video:{str(event.subject.get('video_id') or '')}"

    def _state_key(self, channel_id: str) -> str:
        return f"youtube_channel_rss:{channel_id}"

    def _hydrate_channel_state(self, channel_id: str) -> None:
        if channel_id in self._seeded_by_channel:
            return
        raw = self._load_state_fn(self._state_key(channel_id))
        if not isinstance(raw, dict):
            self._seeded_by_channel[channel_id] = False
            return
        self._seeded_by_channel[channel_id] = bool(raw.get("seeded", False))
        seen_ids = raw.get("seen_ids")
        if isinstance(seen_ids, list):
            cleaned = [str(x).strip() for x in seen_ids if str(x).strip()]
            if cleaned:
                self._seen_by_channel[channel_id] = set(cleaned[: self._max_seen_cache])
        etag = raw.get("etag")
        if isinstance(etag, str) and etag.strip():
            self._etag_by_channel[channel_id] = etag.strip()
        modified = raw.get("last_modified")
        if isinstance(modified, str) and modified.strip():
            self._modified_by_channel[channel_id] = modified.strip()

    def _persist_channel_state(self, channel_id: str) -> None:
        seen = sorted(self._seen_by_channel.get(channel_id, set()))
        state_payload = {
            "seeded": bool(self._seeded_by_channel.get(channel_id, False)),
            "seen_ids": seen[: self._max_seen_cache],
            "etag": self._etag_by_channel.get(channel_id, ""),
            "last_modified": self._modified_by_channel.get(channel_id, ""),
        }
        self._save_state_fn(self._state_key(channel_id), state_payload)


def _safe_text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _is_youtube_short_url(url: str) -> bool:
    return "/shorts/" in str(url or "").lower()


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
