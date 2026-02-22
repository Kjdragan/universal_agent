"""YouTube channel RSS adapter."""

from __future__ import annotations

import json
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from csi_ingester.adapters.base import RawEvent, SourceAdapter
from csi_ingester.contract import CreatorSignalEvent

ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
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
        self._watchlist_file_mtime: float | None = None
        self._watchlist_file_channels: list[dict[str, str]] = []
        self._load_state_fn = lambda source_key: None
        self._save_state_fn = lambda source_key, state: None

    def set_state_backend(self, load_state_fn, save_state_fn) -> None:
        self._load_state_fn = load_state_fn
        self._save_state_fn = save_state_fn

    async def fetch_events(self) -> list[RawEvent]:
        watchlist = self._resolve_watchlist()
        timeout_seconds = max(5, int(self.config.get("timeout_seconds", 20)))
        events: list[RawEvent] = []
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            for item in watchlist:
                channel_id = str(item.get("channel_id") or "").strip()
                channel_name = str(item.get("channel_name") or "").strip()
                if not channel_id:
                    continue
                self._hydrate_channel_state(channel_id)
                entries = await self._fetch_channel_entries(client, channel_id=channel_id)
                if not entries:
                    self._persist_channel_state(channel_id)
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
        return events

    def _resolve_watchlist(self) -> list[dict[str, str]]:
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

        return merged

    def _load_watchlist_file_channels(self) -> list[dict[str, str]]:
        if not self._watchlist_file:
            return []
        path = Path(self._watchlist_file).expanduser()
        if not path.exists():
            if self._watchlist_file_channels:
                logger.warning("RSS watchlist file missing path=%s; keeping previous cached list", path)
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
        response = await client.get(
            "https://www.youtube.com/feeds/videos.xml",
            params={"channel_id": channel_id},
            headers=headers,
        )
        if response.status_code == 304:
            return []
        if response.status_code >= 400:
            return []
        if "etag" in response.headers:
            self._etag_by_channel[channel_id] = response.headers["etag"]
        if "last-modified" in response.headers:
            self._modified_by_channel[channel_id] = response.headers["last-modified"]

        root = ET.fromstring(response.text)
        entries: list[dict[str, Any]] = []
        for entry in root.findall("a:entry", ATOM_NS):
            video_id = _safe_text(entry.find("yt:videoId", ATOM_NS))
            if not video_id:
                continue
            link = entry.find("a:link", ATOM_NS)
            href = link.attrib.get("href", "").strip() if link is not None else ""
            published_at = _safe_text(entry.find("a:published", ATOM_NS)) or _iso_now()
            entries.append(
                {
                    "video_id": video_id,
                    "channel_id": channel_id,
                    "url": href or f"https://www.youtube.com/watch?v={video_id}",
                    "title": _safe_text(entry.find("a:title", ATOM_NS)),
                    "published_at": published_at,
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
            "published_at": str(payload.get("published_at") or now),
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


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
