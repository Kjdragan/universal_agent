"""YouTube playlist adapter."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from csi_ingester.adapters.base import RawEvent, SourceAdapter
from csi_ingester.contract import CreatorSignalEvent


class YouTubePlaylistAdapter(SourceAdapter):
    """Detect newly added playlist videos via YouTube Data API."""

    def __init__(self, config: dict):
        self.config = config
        self._seen_by_playlist: dict[str, set[str]] = {}
        self._seeded_by_playlist: dict[str, bool] = {}
        self._seed_on_first_run = bool(config.get("seed_on_first_run", True))
        self._max_seen_cache = max(50, int(config.get("max_seen_cache_per_playlist", 1000)))
        self._state_by_playlist: dict[str, dict[str, float | str]] = {}
        self._load_state_fn = lambda source_key: None
        self._save_state_fn = lambda source_key, state: None
        self._default_interval_seconds = max(5.0, float(config.get("poll_interval_seconds", 60)))
        adaptive = config.get("adaptive_polling") if isinstance(config.get("adaptive_polling"), dict) else {}
        self._adaptive_enabled = bool((adaptive or {}).get("enabled", False))
        self._active_interval_seconds = max(5.0, float((adaptive or {}).get("active_interval_seconds", self._default_interval_seconds)))
        self._idle_interval_seconds = max(self._active_interval_seconds, float((adaptive or {}).get("idle_interval_seconds", 300)))
        self._activity_threshold_seconds = max(
            60.0, float((adaptive or {}).get("activity_threshold_minutes", 15)) * 60.0
        )
        self._daily_quota_limit = max(1, int(config.get("quota_limit", 10_000)))
        self._min_quota_buffer = max(0, int((adaptive or {}).get("min_quota_buffer", 1_000)))
        self._quota_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._quota_used = 0

    def set_state_backend(self, load_state_fn, save_state_fn) -> None:
        self._load_state_fn = load_state_fn
        self._save_state_fn = save_state_fn

    async def fetch_events(self) -> list[RawEvent]:
        api_key = (os.getenv(self.config.get("api_key_env") or "YOUTUBE_API_KEY") or "").strip()
        playlists = list(self.config.get("playlists") or [])
        if not api_key or not playlists:
            return []

        self._rollover_quota_if_needed()
        timeout_seconds = max(5, int(self.config.get("timeout_seconds", 20)))
        now_ts = time.time()
        events: list[RawEvent] = []
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            for playlist in playlists:
                playlist_id = str(playlist.get("id") or "").strip()
                if not playlist_id:
                    continue
                self._hydrate_playlist_state(playlist_id)
                if not self._should_poll(playlist_id, now_ts):
                    continue
                if not self._consume_quota(1):
                    self._set_next_poll(playlist_id, detected_new=False, now_ts=now_ts, force_idle=True)
                    self._persist_playlist_state(playlist_id)
                    continue
                items = await self._fetch_playlist_items(client, api_key=api_key, playlist_id=playlist_id)
                if not items:
                    self._set_next_poll(playlist_id, detected_new=False, now_ts=now_ts)
                    self._persist_playlist_state(playlist_id)
                    continue
                # Sort items newest first to establish the stable head of the playlist
                items.sort(key=lambda x: str(x.get("occurred_at") or ""), reverse=True)
                items = items[: self._max_seen_cache]
                
                seen = self._seen_by_playlist.setdefault(playlist_id, set())
                current_ids = [item["video_id"] for item in items if item.get("video_id")]
                seeded = self._seeded_by_playlist.get(playlist_id, False)
                if not seen and self._seed_on_first_run and not seeded:
                    seen.update(current_ids)
                    self._seeded_by_playlist[playlist_id] = True
                    self._set_next_poll(playlist_id, detected_new=False, now_ts=now_ts)
                    self._persist_playlist_state(playlist_id)
                    continue

                new_items = [item for item in items if item.get("video_id") and item["video_id"] not in seen]
                # Re-reverse to emit oldest first for regular chronological flow.
                for item in reversed(new_items):
                    events.append(
                        RawEvent(
                            source="youtube_playlist",
                            event_type="video_added_to_playlist",
                            payload=item,
                            occurred_at=str(item.get("occurred_at") or _iso_now()),
                        )
                    )
                    seen.add(item["video_id"])
                self._set_next_poll(playlist_id, detected_new=bool(new_items), now_ts=now_ts)
                self._seeded_by_playlist[playlist_id] = True
                # Keep only IDs currently present to prevent unbounded growth.
                self._seen_by_playlist[playlist_id] = set(current_ids[: self._max_seen_cache])
                self._persist_playlist_state(playlist_id)
        return events

    async def _fetch_playlist_items(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: str,
        playlist_id: str,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params = {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": 50,
                "key": api_key,
            }
            if page_token:
                params["pageToken"] = page_token
            response = await client.get("https://www.googleapis.com/youtube/v3/playlistItems", params=params)
            if response.status_code >= 400:
                break
            body = response.json() if response.content else {}
            for row in body.get("items", []) or []:
                snippet = row.get("snippet") or {}
                resource = snippet.get("resourceId") or {}
                content = row.get("contentDetails") or {}
                video_id = str(resource.get("videoId") or content.get("videoId") or "").strip()
                if not video_id:
                    continue
                occurred_at = str(snippet.get("publishedAt") or content.get("videoPublishedAt") or _iso_now())
                items.append(
                    {
                        "playlist_id": playlist_id,
                        "video_id": video_id,
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                        "title": str(snippet.get("title") or ""),
                        "channel_id": str(snippet.get("videoOwnerChannelId") or snippet.get("channelId") or ""),
                        "published_at": str(content.get("videoPublishedAt") or snippet.get("publishedAt") or occurred_at),
                        "occurred_at": occurred_at,
                    }
                )
            page_token = str(body.get("nextPageToken") or "").strip()
            if not page_token:
                break
        return items

    def normalize(self, raw: RawEvent) -> CreatorSignalEvent:
        now = _iso_now()
        payload = raw.payload
        video_id = str(payload.get("video_id") or "")
        playlist_id = str(payload.get("playlist_id") or "")
        subject = {
            "platform": "youtube",
            "video_id": video_id,
            "playlist_id": playlist_id,
            "url": str(payload.get("url") or f"https://www.youtube.com/watch?v={video_id}"),
            "title": str(payload.get("title") or ""),
            "channel_id": str(payload.get("channel_id") or ""),
            "published_at": str(payload.get("published_at") or now),
        }
        event_id = str(payload.get("event_id") or f"yt:playlist:{video_id}:{playlist_id}:{int(datetime.now(timezone.utc).timestamp())}")
        dedupe_key = f"youtube:video:{video_id}:{playlist_id}"
        return CreatorSignalEvent(
            event_id=event_id,
            dedupe_key=dedupe_key,
            source="youtube_playlist",
            event_type="video_added_to_playlist",
            occurred_at=raw.occurred_at,
            received_at=now,
            subject=subject,
            routing={"pipeline": "youtube_tutorial_explainer", "priority": "urgent", "tags": ["youtube", "playlist"]},
            metadata={"source_adapter": "youtube_playlist_v1"},
        )

    def get_dedupe_key(self, event: CreatorSignalEvent) -> str:
        subject = event.subject
        video_id = str(subject.get("video_id") or "")
        playlist_id = str(subject.get("playlist_id") or "")
        return f"youtube:video:{video_id}:{playlist_id}"

    def _rollover_quota_if_needed(self) -> None:
        current_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if current_day != self._quota_day:
            self._quota_day = current_day
            self._quota_used = 0

    def _remaining_quota(self) -> int:
        return max(0, self._daily_quota_limit - self._quota_used)

    def _consume_quota(self, units: int) -> bool:
        units = max(0, int(units))
        if self._remaining_quota() < units:
            return False
        self._quota_used += units
        return True

    def _is_active(self, playlist_id: str, now_ts: float) -> bool:
        state = self._state_by_playlist.get(playlist_id) or {}
        last_new = float(state.get("last_new_ts") or 0.0)
        if not last_new:
            return False
        return (now_ts - last_new) <= self._activity_threshold_seconds

    def _should_poll(self, playlist_id: str, now_ts: float) -> bool:
        state = self._state_by_playlist.get(playlist_id) or {}
        next_check = float(state.get("next_check_ts") or 0.0)
        return now_ts >= next_check

    def _set_next_poll(self, playlist_id: str, *, detected_new: bool, now_ts: float, force_idle: bool = False) -> None:
        state = self._state_by_playlist.setdefault(playlist_id, {})
        if detected_new:
            state["last_new_ts"] = now_ts
        mode = "idle"
        if not force_idle and self._adaptive_enabled and self._is_active(playlist_id, now_ts):
            mode = "active"
        if not force_idle and not self._adaptive_enabled:
            interval = self._default_interval_seconds
        elif mode == "active":
            interval = self._active_interval_seconds
        else:
            interval = self._idle_interval_seconds
        if self._remaining_quota() < self._min_quota_buffer:
            mode = "idle"
            interval = max(interval, self._idle_interval_seconds)
        state["mode"] = mode
        state["next_check_ts"] = now_ts + max(5.0, interval)

    def _state_key(self, playlist_id: str) -> str:
        return f"youtube_playlist:{playlist_id}"

    def _hydrate_playlist_state(self, playlist_id: str) -> None:
        if playlist_id in self._seeded_by_playlist:
            return
        raw = self._load_state_fn(self._state_key(playlist_id))
        if not isinstance(raw, dict):
            self._seeded_by_playlist[playlist_id] = False
            return
        self._seeded_by_playlist[playlist_id] = bool(raw.get("seeded", False))
        seen_ids = raw.get("seen_ids")
        if isinstance(seen_ids, list):
            cleaned = [str(x).strip() for x in seen_ids if str(x).strip()]
            if cleaned:
                self._seen_by_playlist[playlist_id] = set(cleaned[: self._max_seen_cache])
        state = raw.get("poll_state")
        if isinstance(state, dict):
            hydrated: dict[str, float | str] = {}
            for key in ("next_check_ts", "last_new_ts"):
                if key in state:
                    try:
                        hydrated[key] = float(state[key])
                    except Exception:
                        pass
            mode = state.get("mode")
            if isinstance(mode, str) and mode in {"idle", "active"}:
                hydrated["mode"] = mode
            if hydrated:
                self._state_by_playlist[playlist_id] = hydrated

    def _persist_playlist_state(self, playlist_id: str) -> None:
        seen = sorted(self._seen_by_playlist.get(playlist_id, set()))
        state_payload = {
            "seeded": bool(self._seeded_by_playlist.get(playlist_id, False)),
            "seen_ids": seen[: self._max_seen_cache],
            "poll_state": self._state_by_playlist.get(playlist_id, {}),
        }
        self._save_state_fn(self._state_key(playlist_id), state_payload)


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
