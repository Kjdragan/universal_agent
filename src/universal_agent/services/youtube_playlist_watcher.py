"""Native UA YouTube Playlist Watcher.

Polls the YouTube Data API for new videos added to YT_TUTORIALS_PLAYLIST_ID
and dispatches them into the hooks pipeline (youtube-expert agent).

This replaces the CSI Ingester youtube_playlist source so the tutorial
pipeline is self-contained inside UA and does not depend on a separate VPS
process being alive.

Env vars:
    YT_TUTORIALS_PLAYLIST_ID    — playlist to watch (required)
    YOUTUBE_API_KEY             — YouTube Data API key (required)
    YT_TUTORIALS_POLL_INTERVAL_SECONDS — poll cadence, default 60
    UA_YT_PLAYLIST_WATCHER_ENABLED — set to "0" / "false" to disable
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

import httpx

logger = logging.getLogger(__name__)

_YOUTUBE_API_PLAYLIST_ITEMS = "https://www.googleapis.com/youtube/v3/playlistItems"
_STATE_FILENAME = "youtube_playlist_watcher_state.json"


def _is_enabled() -> bool:
    val = os.getenv("UA_YT_PLAYLIST_WATCHER_ENABLED", "1").strip().lower()
    return val not in {"0", "false", "no", "off"}


def _poll_interval() -> float:
    try:
        return max(15.0, float(os.getenv("YT_TUTORIALS_POLL_INTERVAL_SECONDS", "60")))
    except Exception:
        return 60.0


def _state_path() -> Path:
    ops_dir = Path(
        os.getenv("UA_OPS_DIR", "")
        or os.getenv("UA_OPS_CONFIG_PATH", "AGENT_RUN_WORKSPACES/ops_config.json")
    )
    if ops_dir.suffix:
        ops_dir = ops_dir.parent
    ops_dir.mkdir(parents=True, exist_ok=True)
    return ops_dir / _STATE_FILENAME


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


DispatchFn = Callable[[str, dict[str, Any]], Coroutine[Any, Any, tuple[bool, str]]]
NotifyFn = Callable[[dict[str, Any]], None]


class YouTubePlaylistWatcher:
    """Async polling service for a single YouTube tutorial playlist."""

    def __init__(
        self,
        *,
        dispatch_fn: DispatchFn,
        notification_sink: Optional[NotifyFn] = None,
    ) -> None:
        self._dispatch_fn = dispatch_fn
        self._notification_sink = notification_sink
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        # runtime status (for ops endpoint)
        self._enabled = _is_enabled()
        self._last_poll_at: Optional[str] = None
        self._last_poll_ok: Optional[bool] = None
        self._last_error: str = ""
        self._seen_count: int = 0
        self._dispatched_total: int = 0
        self._poll_count: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self._enabled:
            logger.info("📺 YouTube playlist watcher DISABLED (UA_YT_PLAYLIST_WATCHER_ENABLED=0)")
            return
        playlist_id = os.getenv("YT_TUTORIALS_PLAYLIST_ID", "").strip()
        api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
        if not playlist_id or not api_key:
            logger.warning(
                "📺 YouTube playlist watcher not started: "
                "YT_TUTORIALS_PLAYLIST_ID=%r YOUTUBE_API_KEY=%s",
                playlist_id or "(unset)",
                "(set)" if api_key else "(unset)",
            )
            self._enabled = False
            return
        logger.info(
            "📺 YouTube playlist watcher started playlist_id=%s poll_interval=%.0fs",
            playlist_id,
            _poll_interval(),
        )
        state = _load_state()
        seen: set[str] = set(state.get("seen_ids", []))
        self._seen_count = len(seen)
        self._task = asyncio.create_task(self._loop(playlist_id, api_key, seen))

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except Exception:
                self._task.cancel()
            self._task = None

    # ------------------------------------------------------------------
    # Status (for ops endpoint)
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        playlist_id = os.getenv("YT_TUTORIALS_PLAYLIST_ID", "").strip()
        return {
            "enabled": self._enabled,
            "playlist_id": playlist_id,
            "poll_interval_seconds": _poll_interval(),
            "last_poll_at": self._last_poll_at,
            "last_poll_ok": self._last_poll_ok,
            "last_error": self._last_error,
            "seen_count": self._seen_count,
            "dispatched_total": self._dispatched_total,
            "poll_count": self._poll_count,
        }

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    async def _loop(self, playlist_id: str, api_key: str, seen: set[str]) -> None:
        # Seed on first run — mark all existing items as seen so we only
        # emit events for videos added after the watcher starts.
        seeded = False
        while not self._stop_event.is_set():
            try:
                items = await self._fetch_playlist_items(playlist_id, api_key)
                self._last_poll_at = _iso_now()
                self._poll_count += 1
                if items is None:
                    self._last_poll_ok = False
                    await self._sleep_or_stop(_poll_interval())
                    continue
                current_ids = [item["video_id"] for item in items if item.get("video_id")]
                if not seeded:
                    seen.update(current_ids)
                    seeded = True
                    self._seen_count = len(seen)
                    self._last_poll_ok = True
                    logger.info(
                        "📺 Playlist watcher seeded playlist_id=%s seen=%d",
                        playlist_id,
                        len(seen),
                    )
                    _save_state({"seen_ids": list(seen), "seeded_at": _iso_now()})
                    await self._sleep_or_stop(_poll_interval())
                    continue

                new_items = [i for i in items if i.get("video_id") and i["video_id"] not in seen]
                self._last_poll_ok = True
                self._last_error = ""
                for item in reversed(new_items):
                    vid = item["video_id"]
                    seen.add(vid)
                    self._seen_count = len(seen)
                    self._dispatched_total += 1
                    logger.info(
                        "📺 New playlist video detected video_id=%s title=%r",
                        vid,
                        item.get("title", ""),
                    )
                    self._emit_notification(
                        kind="youtube_playlist_new_video",
                        title="New Tutorial Video Detected",
                        message=f"{item.get('title') or vid} — queued for processing",
                        severity="info",
                        metadata={
                            "video_id": vid,
                            "video_url": item.get("url", f"https://www.youtube.com/watch?v={vid}"),
                            "title": item.get("title", ""),
                            "playlist_id": playlist_id,
                        },
                    )
                    await self._dispatch(item)

                # Trim seen to prevent unbounded growth (keep newest 1 000)
                if len(seen) > 1000:
                    seen = set(current_ids[:1000])
                _save_state({"seen_ids": list(seen), "updated_at": _iso_now()})
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._last_poll_ok = False
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._last_poll_at = _iso_now()
                logger.exception("📺 Playlist watcher poll error: %s", exc)
            await self._sleep_or_stop(_poll_interval())

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # ------------------------------------------------------------------
    # YouTube API
    # ------------------------------------------------------------------

    async def _fetch_playlist_items(
        self, playlist_id: str, api_key: str
    ) -> Optional[list[dict[str, Any]]]:
        items: list[dict[str, Any]] = []
        page_token = ""
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                while True:
                    params: dict[str, str] = {
                        "part": "snippet,contentDetails",
                        "playlistId": playlist_id,
                        "maxResults": "50",
                        "key": api_key,
                    }
                    if page_token:
                        params["pageToken"] = page_token
                    resp = await client.get(_YOUTUBE_API_PLAYLIST_ITEMS, params=params)
                    if resp.status_code == 403:
                        logger.warning(
                            "📺 YouTube API quota exceeded or auth error status=%d", resp.status_code
                        )
                        self._last_error = f"youtube_api_{resp.status_code}"
                        return None
                    if resp.status_code >= 400:
                        logger.warning("📺 YouTube API error status=%d", resp.status_code)
                        self._last_error = f"youtube_api_{resp.status_code}"
                        return None
                    body = resp.json() if resp.content else {}
                    for row in body.get("items", []) or []:
                        snippet = row.get("snippet") or {}
                        resource = snippet.get("resourceId") or {}
                        content = row.get("contentDetails") or {}
                        video_id = str(
                            resource.get("videoId") or content.get("videoId") or ""
                        ).strip()
                        if not video_id:
                            continue
                        occurred_at = str(
                            snippet.get("publishedAt")
                            or content.get("videoPublishedAt")
                            or _iso_now()
                        )
                        items.append(
                            {
                                "video_id": video_id,
                                "url": f"https://www.youtube.com/watch?v={video_id}",
                                "title": str(snippet.get("title") or ""),
                                "channel_id": str(
                                    snippet.get("videoOwnerChannelId")
                                    or snippet.get("channelId")
                                    or ""
                                ),
                                "occurred_at": occurred_at,
                                "playlist_id": playlist_id,
                            }
                        )
                    page_token = str(body.get("nextPageToken") or "").strip()
                    if not page_token:
                        break
            return items
        except Exception as exc:
            logger.warning("📺 Playlist fetch failed: %s", exc)
            self._last_error = f"{type(exc).__name__}: {exc}"
            return None

    # ------------------------------------------------------------------
    # Dispatch + notification helpers
    # ------------------------------------------------------------------

    async def _dispatch(self, item: dict[str, Any]) -> None:
        payload = {
            "video_url": item.get("url", f"https://www.youtube.com/watch?v={item['video_id']}"),
            "video_id": item.get("video_id", ""),
            "channel_id": item.get("channel_id", ""),
            "title": item.get("title", ""),
            "mode": "explainer_only",
            "allow_degraded_transcript_only": True,
            "source": "yt_playlist_watcher",
        }
        try:
            ok, reason = await self._dispatch_fn("youtube/manual", payload)
            if ok:
                logger.info(
                    "📺 Dispatched tutorial pipeline video_id=%s", item.get("video_id")
                )
            else:
                logger.warning(
                    "📺 Dispatch rejected video_id=%s reason=%s",
                    item.get("video_id"),
                    reason,
                )
                self._emit_notification(
                    kind="youtube_playlist_dispatch_failed",
                    title="Tutorial Dispatch Rejected",
                    message=f"{item.get('title') or item.get('video_id')}: {reason}",
                    severity="warning",
                    metadata={"video_id": item.get("video_id", ""), "reason": reason},
                )
        except Exception as exc:
            logger.exception(
                "📺 Dispatch error video_id=%s: %s", item.get("video_id"), exc
            )

    def _emit_notification(
        self,
        *,
        kind: str,
        title: str,
        message: str,
        severity: str = "info",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        if not self._notification_sink:
            return
        try:
            self._notification_sink(
                {
                    "kind": kind,
                    "title": title,
                    "message": message,
                    "severity": severity,
                    "metadata": metadata or {},
                }
            )
        except Exception:
            logger.exception("📺 Failed emitting watcher notification kind=%s", kind)


    async def poll_now(self) -> dict[str, Any]:
        """Manually trigger one poll cycle. Returns result summary."""
        playlist_id = os.getenv("YT_TUTORIALS_PLAYLIST_ID", "").strip()
        api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
        if not playlist_id or not api_key:
            return {"ok": False, "reason": "missing_config"}
        state = _load_state()
        seen: set[str] = set(state.get("seen_ids", []))
        items = await self._fetch_playlist_items(playlist_id, api_key)
        if items is None:
            return {"ok": False, "reason": self._last_error or "fetch_failed"}
        self._last_poll_at = _iso_now()
        self._poll_count += 1
        self._last_poll_ok = True
        current_ids = [i["video_id"] for i in items if i.get("video_id")]
        new_items = [i for i in items if i.get("video_id") and i["video_id"] not in seen]
        dispatched = []
        for item in reversed(new_items):
            vid = item["video_id"]
            seen.add(vid)
            self._seen_count = len(seen)
            self._dispatched_total += 1
            self._emit_notification(
                kind="youtube_playlist_new_video",
                title="New Tutorial Video Detected",
                message=f"{item.get('title') or vid} — queued for processing",
                severity="info",
                metadata={
                    "video_id": vid,
                    "video_url": item.get("url", f"https://www.youtube.com/watch?v={vid}"),
                    "title": item.get("title", ""),
                    "playlist_id": playlist_id,
                },
            )
            await self._dispatch(item)
            dispatched.append(vid)
        if len(seen) > 1000:
            seen = set(current_ids[:1000])
        _save_state({"seen_ids": list(seen), "updated_at": _iso_now()})
        return {
            "ok": True,
            "total_in_playlist": len(items),
            "new_dispatched": len(dispatched),
            "dispatched_video_ids": dispatched,
            "polled_at": self._last_poll_at,
        }
