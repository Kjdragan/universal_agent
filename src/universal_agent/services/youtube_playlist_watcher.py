"""Native UA YouTube Playlist Watcher.

Polls the YouTube Data API for new videos added to YT_TUTORIALS_PLAYLIST_ID
and dispatches them into the hooks pipeline (youtube-expert agent).

This replaces the CSI Ingester youtube_playlist source so the tutorial
pipeline is self-contained inside UA and does not depend on a separate VPS
process being alive.

Env vars:
    YT_TUTORIALS_PLAYLIST_ID    — playlist to watch (required)
    YOUTUBE_API_KEY             — YouTube Data API key (required)
    YT_TUTORIALS_POLL_INTERVAL_SECONDS — poll cadence, default 120
    UA_YT_PLAYLIST_WATCHER_ENABLED — set to "0" / "false" to disable
    YT_TUTORIALS_RUN_GRACE_SECONDS  — time to wait before treating a dispatched
                                       video without artifacts as failed (default: 2400)
    YT_TUTORIALS_MAX_RUN_RETRIES    — max re-dispatch attempts for crashed runs
                                       (default: 2)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Callable, Coroutine, Optional
from xml.etree import ElementTree as ET

import httpx

from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.youtube_mode_utils import (
    infer_youtube_mode,
)

logger = logging.getLogger(__name__)

_YOUTUBE_API_PLAYLIST_ITEMS = "https://www.googleapis.com/youtube/v3/playlistItems"
_YOUTUBE_RSS_FEED = "https://www.youtube.com/feeds/videos.xml"
_STATE_FILENAME = "youtube_playlist_watcher_state.json"
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
_TUTORIAL_ARTIFACT_DIR_CANONICAL = "youtube-tutorial-creation"
_TUTORIAL_NOT_READY_STATUSES = {
    "",
    "unknown",
    "failed",
    "dispatch_failed",
    "failed_local_ingest",
    "pending_local_ingest",
    "timed_out",
}


def _is_enabled() -> bool:
    raw = os.getenv("UA_YT_PLAYLIST_WATCHER_ENABLED")
    if raw is not None and raw.strip():
        return raw.strip().lower() not in {"0", "false", "no", "off"}
    profile = (os.getenv("UA_DEPLOYMENT_PROFILE") or "local_workstation").strip().lower()
    if profile == "local_workstation":
        return False
    return True


def _poll_interval() -> float:
    try:
        return max(15.0, float(os.getenv("YT_TUTORIALS_POLL_INTERVAL_SECONDS", "120")))
    except Exception:
        return 120.0


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




def _tutorial_manifest_video_id(manifest_payload: dict[str, Any]) -> str:
    if not isinstance(manifest_payload, dict):
        return ""
    top_level = str(manifest_payload.get("video_id") or "").strip()
    if top_level:
        return top_level
    video_block = manifest_payload.get("video")
    if isinstance(video_block, dict):
        nested = str(video_block.get("video_id") or "").strip()
        if nested:
            return nested
    return ""


def _tutorial_manifest_is_processed(manifest_payload: dict[str, Any]) -> bool:
    status = str(manifest_payload.get("status") or "").strip().lower()
    return status not in _TUTORIAL_NOT_READY_STATUSES


def _video_has_processed_tutorial_artifacts(video_id: str) -> bool:
    vid = str(video_id or "").strip()
    if not vid:
        return False
    try:
        tutorials_root = resolve_artifacts_dir() / _TUTORIAL_ARTIFACT_DIR_CANONICAL
    except Exception:
        return False
    if not tutorials_root.exists():
        return False
    for manifest_path in tutorials_root.rglob("manifest.json"):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if _tutorial_manifest_video_id(payload) != vid:
            continue
        if not _tutorial_manifest_is_processed(payload):
            continue
        run_dir = manifest_path.parent
        if (run_dir / "README.md").is_file() and (run_dir / "CONCEPT.md").is_file():
            return True
    return False


DispatchResult = tuple[bool, str] | dict[str, Any]
DispatchFn = Callable[[str, dict[str, Any]], Coroutine[Any, Any, DispatchResult]]
NotifyFn = Callable[[dict[str, Any]], None]


def _run_grace_seconds() -> float:
    """Grace period before treating a dispatched video without artifacts as failed."""
    try:
        return max(120.0, float(os.getenv("YT_TUTORIALS_RUN_GRACE_SECONDS", "2400")))
    except Exception:
        return 2400.0


def _max_run_retries() -> int:
    """Max re-dispatch attempts for videos whose hook sessions crashed."""
    try:
        return max(1, min(5, int(os.getenv("YT_TUTORIALS_MAX_RUN_RETRIES", "2"))))
    except Exception:
        return 2


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
        # Shared reference to the authoritative seen set so poll_now can
        # access the same set as the background _loop.
        self._seen_ids: set[str] = set()
        self._poll_count: int = 0
        # In-process dedup guard for inflight dispatches only. We intentionally
        # do NOT treat "detected" as "processed"; a video is persisted to the
        # seen set only after the dispatch handoff is accepted. That allows the
        # watcher to retry naturally after crashes or rejected dispatch attempts,
        # with durable run admission acting as the authoritative dedupe layer.
        self._inflight_dispatches: set[str] = set()
        # Track videos that already sent a "Dispatch Delayed" notification
        # to suppress duplicate Telegram messages on consecutive poll cycles.
        self._notified_delayed_videos: set[str] = set()
        # Videos whose dispatch failed with a retryable reason (DB lock).
        # Kept here so next poll retries silently without re-notification.
        self._pending_dispatch_items: dict[str, dict[str, Any]] = {}
        # Mutex that serialises the inflight/seen transition so _loop and
        # poll_now cannot both dispatch the same unseen video simultaneously.
        self._dispatch_lock: asyncio.Lock = asyncio.Lock()
        # ── Post-dispatch run monitoring state ──
        # Tracks when each video was first dispatched (epoch timestamp).
        self._dispatch_timestamps: dict[str, float] = {}
        # Counts how many times a seen-but-unprocessed video has been retried.
        self._run_retry_counts: dict[str, int] = {}
        # Videos that exhausted all retry attempts and are permanently failed.
        self._permanently_failed_videos: set[str] = set()

    @staticmethod
    def _sanitize_pending_state(raw_pending: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(raw_pending, dict):
            return {}
        pending: dict[str, dict[str, Any]] = {}
        for raw_video_id, raw_item in raw_pending.items():
            video_id = str(raw_video_id or "").strip()
            if not video_id or not isinstance(raw_item, dict):
                continue
            item_video_id = str(raw_item.get("video_id") or "").strip()
            if item_video_id and item_video_id != video_id:
                continue
            if not item_video_id:
                raw_item = dict(raw_item)
                raw_item["video_id"] = video_id
            pending[video_id] = dict(raw_item)
        return pending

    @staticmethod
    def _normalize_dispatch_result(result: DispatchResult) -> tuple[bool, str, dict[str, Any]]:
        if isinstance(result, dict):
            payload = dict(result)
            decision = str(payload.get("decision") or "").strip().lower()
            reason = str(payload.get("reason") or decision or "dispatch_result").strip()
            ok = decision not in {"failed", "error"}
            return ok, reason, payload
        if isinstance(result, tuple) and len(result) == 2:
            ok, reason = result
            return bool(ok), str(reason or ""), {}
        return False, "invalid_dispatch_result", {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self._enabled:
            logger.info("📺 YouTube playlist watcher DISABLED (UA_YT_PLAYLIST_WATCHER_ENABLED=0)")
            return
        playlist_id = os.getenv("YT_TUTORIALS_PLAYLIST_ID", "").strip()
        api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
        if not playlist_id:
            logger.warning(
                "📺 YouTube playlist watcher not started: "
                "YT_TUTORIALS_PLAYLIST_ID=%r",
                playlist_id or "(unset)",
            )
            self._enabled = False
            return
        logger.info(
            "📺 YouTube playlist watcher started playlist_id=%s poll_interval=%.0fs api_key=%s",
            playlist_id,
            _poll_interval(),
            "(set)" if api_key else "(unset; rss fallback only)",
        )
        state = _load_state()
        seen: set[str] = set(state.get("seen_ids", []))
        self._pending_dispatch_items = self._sanitize_pending_state(state.get("pending_dispatch_items"))
        self._notified_delayed_videos = set(
            str(v or "").strip()
            for v in list(state.get("notified_delayed_video_ids") or [])
            if str(v or "").strip()
        )
        # Restore post-dispatch monitoring state
        raw_timestamps = state.get("dispatch_timestamps")
        if isinstance(raw_timestamps, dict):
            self._dispatch_timestamps = {
                str(k): float(v) for k, v in raw_timestamps.items()
                if str(k or "").strip() and isinstance(v, (int, float))
            }
        raw_retry_counts = state.get("run_retry_counts")
        if isinstance(raw_retry_counts, dict):
            self._run_retry_counts = {
                str(k): int(v) for k, v in raw_retry_counts.items()
                if str(k or "").strip() and isinstance(v, (int, float))
            }
        raw_perm_failed = state.get("permanently_failed_video_ids")
        if isinstance(raw_perm_failed, list):
            self._permanently_failed_videos = {
                str(v or "").strip() for v in raw_perm_failed
                if str(v or "").strip()
            }
        self._prune_recovered_permanently_failed_videos()
        # Prune stale pending entries that already produced artifacts while the
        # watcher was offline (for example recovered by another route).
        for video_id in list(self._pending_dispatch_items):
            if not _video_has_processed_tutorial_artifacts(video_id):
                continue
            self._pending_dispatch_items.pop(video_id, None)
            self._notified_delayed_videos.discard(video_id)
            self._dispatch_timestamps.pop(video_id, None)
            self._run_retry_counts.pop(video_id, None)
            seen.add(video_id)
        seen.update(self._pending_dispatch_items.keys())
        self._seen_ids = seen
        self._seen_count = len(seen)
        self._task = asyncio.create_task(self._loop(playlist_id, api_key, seen))

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=10)
            except Exception:
                task.cancel()
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
            "permanently_failed_count": len(self._permanently_failed_videos),
            "permanently_failed_video_ids": sorted(self._permanently_failed_videos),
        }

    def _prune_recovered_permanently_failed_videos(self) -> int:
        """Clear permanent-failure markers once artifacts prove recovery."""
        recovered = [
            video_id
            for video_id in list(self._permanently_failed_videos)
            if _video_has_processed_tutorial_artifacts(video_id)
        ]
        for video_id in recovered:
            self._permanently_failed_videos.discard(video_id)
            self._dispatch_timestamps.pop(video_id, None)
            self._run_retry_counts.pop(video_id, None)
            self._pending_dispatch_items.pop(video_id, None)
            self._notified_delayed_videos.discard(video_id)
            logger.info(
                "📺 Run monitoring: cleared recovered permanent failure marker for video_id=%s",
                video_id,
            )
        return len(recovered)

    def _persist_seen_state(
        self,
        seen: set[str],
        current_ids: list[str],
        *,
        timestamp_key: str,
    ) -> set[str]:
        if len(seen) > 1000:
            preserved_pending = list(self._pending_dispatch_items.keys())[:200]
            seen = set(current_ids[:1000] + preserved_pending)
        self._seen_count = len(seen)
        serialized_pending = {
            video_id: dict(item)
            for video_id, item in self._pending_dispatch_items.items()
            if video_id
        }
        _save_state(
            {
                "seen_ids": list(seen),
                "pending_dispatch_items": serialized_pending,
                "notified_delayed_video_ids": sorted(self._notified_delayed_videos),
                "dispatch_timestamps": dict(self._dispatch_timestamps),
                "run_retry_counts": dict(self._run_retry_counts),
                "permanently_failed_video_ids": sorted(self._permanently_failed_videos),
                timestamp_key: _iso_now(),
            }
        )
        return seen

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
                    seen = self._persist_seen_state(seen, current_ids, timestamp_key="seeded_at")
                    self._last_poll_ok = True
                    self._last_error = ""
                    logger.info(
                        "📺 Playlist watcher seeded playlist_id=%s seen=%d",
                        playlist_id,
                        len(seen),
                    )
                    await self._sleep_or_stop(_poll_interval())
                    continue

                new_items = [i for i in items if i.get("video_id") and i["video_id"] not in seen]
                self._last_poll_ok = True
                self._last_error = ""

                # ── Retry pending (previously failed) dispatches silently ──
                for vid in list(self._pending_dispatch_items):
                    pending_item = self._pending_dispatch_items[vid]
                    if _video_has_processed_tutorial_artifacts(vid):
                        async with self._dispatch_lock:
                            seen.add(vid)
                            self._persist_seen_state(seen, current_ids, timestamp_key="updated_at")
                        self._pending_dispatch_items.pop(vid, None)
                        self._notified_delayed_videos.discard(vid)
                        logger.info(
                            "📺 Cleared stale pending dispatch for already-processed video_id=%s",
                            vid,
                        )
                        continue
                    logger.info("📺 Retrying pending dispatch video_id=%s", vid)
                    dispatched = await self._dispatch(pending_item, silent=True)
                    if dispatched:
                        async with self._dispatch_lock:
                            seen.add(vid)
                            self._persist_seen_state(seen, current_ids, timestamp_key="updated_at")
                            self._dispatched_total += 1
                        self._pending_dispatch_items.pop(vid, None)
                        self._notified_delayed_videos.discard(vid)
                        logger.info("📺 Pending dispatch succeeded video_id=%s", vid)
                    # else: stays in pending for next cycle (no new notification)

                # ── Process truly new (never-seen, never-pending) items ──
                for item in reversed(new_items):
                    vid = item.get("video_id", "")
                    if vid in self._pending_dispatch_items:
                        continue  # already tracked as pending, skip
                    dispatched = await self._process_new_video(
                        item, seen, current_ids, playlist_id
                    )
                    if not dispatched:
                        logger.info(
                            "📺 Skipping duplicate dispatch video_id=%s (already dispatched this session)",
                            item["video_id"],
                        )

                # ── Post-dispatch run monitoring: detect crashed runs ──
                await self._check_failed_seen_videos(seen, current_ids)

                # ── Finalize stale YouTube runs in the durable DB ──
                try:
                    from universal_agent.gateway_server import _hooks_service
                    if _hooks_service is not None:
                        await _hooks_service.finalize_stale_youtube_runs()
                except Exception:
                    pass  # best-effort; don't break the poll loop
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._last_poll_ok = False
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._last_poll_at = _iso_now()
                logger.exception("📺 Playlist watcher poll error: %s", exc)
            await self._sleep_or_stop(_poll_interval())

    async def _check_failed_seen_videos(
        self,
        seen: set[str],
        current_ids: list[str],
    ) -> None:
        """Detect dispatched videos that never produced artifacts and retry or fail them.

        Called at the end of each poll cycle. For each video that was dispatched
        more than ``_run_grace_seconds()`` ago without producing tutorial
        artifacts, either:
        - Move it back to ``_pending_dispatch_items`` for re-dispatch (if within
          ``_max_run_retries()``), or
        - Mark it permanently failed with a notification.
        """
        grace = _run_grace_seconds()
        max_retries = _max_run_retries()
        now = time.time()
        self._prune_recovered_permanently_failed_videos()

        # Build candidate set: videos in seen_ids that have a dispatch_timestamp
        # but are NOT already tracked in _pending_dispatch_items, NOT permanently
        # failed, and NOT already confirmed to have artifacts.
        candidates = [
            vid for vid, ts in list(self._dispatch_timestamps.items())
            if vid in seen
            and vid not in self._pending_dispatch_items
            and vid not in self._permanently_failed_videos
            and (now - ts) > grace
        ]

        for vid in candidates:
            # Double-check: maybe artifacts appeared since the last check
            if _video_has_processed_tutorial_artifacts(vid):
                # Artifacts exist — clean up monitoring state
                self._dispatch_timestamps.pop(vid, None)
                self._run_retry_counts.pop(vid, None)
                logger.info(
                    "📺 Run monitoring: video_id=%s has artifacts, clearing monitoring state",
                    vid,
                )
                continue

            retries_so_far = self._run_retry_counts.get(vid, 0)
            elapsed = now - self._dispatch_timestamps[vid]

            if retries_so_far >= max_retries:
                # Exhausted all retries → permanent failure
                self._permanently_failed_videos.add(vid)
                self._dispatch_timestamps.pop(vid, None)
                self._run_retry_counts.pop(vid, None)
                logger.warning(
                    "📺 Run monitoring: video_id=%s permanently failed after %d retries (%.0fs since dispatch)",
                    vid,
                    retries_so_far,
                    elapsed,
                )
                self._emit_notification(
                    kind="youtube_tutorial_permanently_failed",
                    title="Tutorial Permanently Failed",
                    message=f"Video {vid} failed after {retries_so_far} retries ({elapsed / 60:.0f}m since first dispatch)",
                    severity="error",
                    metadata={
                        "video_id": vid,
                        "retries": retries_so_far,
                        "elapsed_seconds": elapsed,
                    },
                )
            else:
                # Move back to pending for re-dispatch
                self._run_retry_counts[vid] = retries_so_far + 1
                # Reset dispatch timestamp for the new attempt grace window
                self._dispatch_timestamps[vid] = now
                # Remove from seen so it gets re-dispatched on next cycle
                seen.discard(vid)
                # We don't have the original item dict, so create a minimal one
                self._pending_dispatch_items[vid] = {"video_id": vid}
                logger.warning(
                    "📺 Run monitoring: video_id=%s still has no artifacts after %.0fs, "
                    "scheduling re-dispatch (retry %d/%d)",
                    vid,
                    elapsed,
                    retries_so_far + 1,
                    max_retries,
                )

        # Persist state changes
        if candidates:
            self._seen_ids = seen
            self._persist_seen_state(seen, current_ids, timestamp_key="updated_at")

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
        api_key = str(api_key or "").strip()
        if api_key:
            items = await self._fetch_playlist_items_via_api(playlist_id, api_key)
            if items is not None:
                return items
            logger.warning(
                "📺 Falling back to playlist RSS feed after API failure playlist_id=%s error=%s",
                playlist_id,
                self._last_error or "unknown_api_error",
            )
        return await self._fetch_playlist_items_via_rss(playlist_id)

    async def _fetch_playlist_items_via_api(
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
                        failure_reason = "youtube_api_403"
                        try:
                            error_body = resp.json() if resp.content else {}
                            reasons = [
                                str(item.get("reason") or "").strip()
                                for item in ((error_body.get("error") or {}).get("errors") or [])
                                if isinstance(item, dict)
                            ]
                            if any(reason == "quotaExceeded" for reason in reasons):
                                failure_reason = "youtube_api_quota_exceeded"
                        except Exception:
                            pass
                        logger.warning(
                            "📺 YouTube API quota exceeded or auth error status=%d", resp.status_code
                        )
                        self._last_error = failure_reason
                        break
                    if resp.status_code >= 400:
                        logger.warning("📺 YouTube API error status=%d", resp.status_code)
                        self._last_error = f"youtube_api_{resp.status_code}"
                        break
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
                                "description": str(
                                    snippet.get("description") or ""
                                ),
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

    async def _fetch_playlist_items_via_rss(self, playlist_id: str) -> Optional[list[dict[str, Any]]]:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(_YOUTUBE_RSS_FEED, params={"playlist_id": playlist_id})
            if resp.status_code >= 400:
                logger.warning("📺 Playlist RSS fetch failed status=%d", resp.status_code)
                self._last_error = f"youtube_playlist_rss_{resp.status_code}"
                return None
            root = ET.fromstring(resp.text)
            items: list[dict[str, Any]] = []
            for entry in root.findall("atom:entry", _ATOM_NS):
                video_id = str(entry.findtext("yt:videoId", default="", namespaces=_ATOM_NS) or "").strip()
                if not video_id:
                    continue
                title = str(entry.findtext("atom:title", default="", namespaces=_ATOM_NS) or "").strip()
                occurred_at = (
                    str(entry.findtext("atom:published", default="", namespaces=_ATOM_NS) or "").strip()
                    or _iso_now()
                )
                channel_id = str(entry.findtext("yt:channelId", default="", namespaces=_ATOM_NS) or "").strip()
                link = ""
                for link_node in entry.findall("atom:link", _ATOM_NS):
                    href = str(link_node.attrib.get("href") or "").strip()
                    rel = str(link_node.attrib.get("rel") or "").strip().lower()
                    if href and (not rel or rel == "alternate"):
                        link = href
                        break
                items.append(
                    {
                        "video_id": video_id,
                        "url": link or f"https://www.youtube.com/watch?v={video_id}",
                        "title": title,
                        "channel_id": channel_id,
                        "occurred_at": occurred_at,
                        "playlist_id": playlist_id,
                    }
                )
            self._last_error = ""
            return items
        except Exception as exc:
            logger.warning("📺 Playlist RSS fetch failed: %s", exc)
            self._last_error = f"{type(exc).__name__}: {exc}"
            return None

    # ------------------------------------------------------------------
    # Dispatch + notification helpers
    # ------------------------------------------------------------------

    async def _dispatch(self, item: dict[str, Any], *, silent: bool = False) -> bool:
        mode = infer_youtube_mode(
            item.get("title"),
            item.get("channel_id"),
            item.get("url"),
            item.get("playlist_id"),
            item.get("description"),
        )
        payload = {
            "video_url": item.get("url", f"https://www.youtube.com/watch?v={item['video_id']}"),
            "video_id": item.get("video_id", ""),
            "channel_id": item.get("channel_id", ""),
            "title": item.get("title", ""),
            "description": item.get("description", ""),
            "mode": mode,
            "allow_degraded_transcript_only": True,
            "source": "yt_playlist_watcher",
        }
        try:
            result = await self._dispatch_fn("youtube/manual", payload)
            ok, reason, details = self._normalize_dispatch_result(result)
            if ok:
                logger.info(
                    "📺 Dispatched tutorial pipeline video_id=%s", item.get("video_id")
                )
                run_id = str(details.get("run_id") or "").strip()
                attempt_id = str(details.get("attempt_id") or "").strip()
                attempt_number = int(details.get("attempt_number") or 0) if str(details.get("attempt_number") or "").strip() else 0
                workspace_dir = str(details.get("workspace_dir") or "").strip()
                decision = str(details.get("decision") or "").strip().lower()
                if run_id or attempt_id or workspace_dir or decision in {"accepted", "skipped", "defer", "attach_to_existing_run"}:
                    if decision in {"skipped"}:
                        title = "YouTube Tutorial Already Tracked"
                        message = (
                            f"{item.get('title') or item.get('video_id')}: existing workflow reused "
                            f"({reason or decision})."
                        )
                        severity = "info"
                    else:
                        title = "YouTube Tutorial Pipeline Queued"
                        attempt_label = attempt_number or 1
                        message = (
                            f"{item.get('title') or item.get('video_id')}: "
                            f"tutorial pipeline attempt {attempt_label} admitted for processing."
                        )
                        severity = "info"
                    self._emit_notification(
                        kind="youtube_tutorial_progress",
                        title=title,
                        message=message,
                        severity=severity,
                        metadata={
                            "video_id": item.get("video_id", ""),
                            "video_url": item.get("url", f"https://www.youtube.com/watch?v={item['video_id']}"),
                            "title": item.get("title", ""),
                            "playlist_id": item.get("playlist_id", ""),
                            "dispatch_decision": decision or "accepted",
                            "dispatch_reason": reason,
                            "run_id": run_id,
                            "attempt_id": attempt_id,
                            "attempt_number": attempt_number or None,
                            "workspace_dir": workspace_dir,
                        },
                    )
                # Clear delayed notification dedup on success
                self._notified_delayed_videos.discard(item.get("video_id", ""))
                return True
            else:
                logger.warning(
                    "📺 Dispatch rejected video_id=%s reason=%s",
                    item.get("video_id"),
                    reason,
                )
                _is_retryable = (
                    str(reason or "").strip().lower() == "runtime_db_locked"
                    or bool(details.get("retryable"))
                )
                _vid = item.get("video_id", "")
                if _is_retryable:
                    # Retryable admission/contention should not create user-facing
                    # notices. The video stays pending and is retried on next poll.
                    self._notified_delayed_videos.add(_vid)
                    if silent:
                        logger.debug("📺 Silent retry failed for video_id=%s reason=%s", _vid, reason)
                    else:
                        logger.info(
                            "📺 Retryable dispatch defer video_id=%s reason=%s (no notification emitted)",
                            _vid,
                            reason,
                        )
                    return False
                # Non-retryable rejections remain user-visible.
                if not silent:
                    self._emit_notification(
                        kind="youtube_playlist_dispatch_failed",
                        title="Tutorial Dispatch Rejected",
                        message=f"{item.get('title') or _vid}: {reason}",
                        severity="warning",
                        metadata={
                            "video_id": item.get("video_id", ""),
                            "reason": reason,
                            "retryable": bool(details.get("retryable")),
                            "run_id": str(details.get("run_id") or "").strip(),
                            "attempt_id": str(details.get("attempt_id") or "").strip(),
                            "attempt_number": details.get("attempt_number"),
                            "workspace_dir": str(details.get("workspace_dir") or "").strip(),
                        },
                    )
                else:
                    logger.debug("📺 Silent non-retryable rejection video_id=%s reason=%s", _vid, reason)
                return False
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            _vid = item.get("video_id", "")
            err_text = str(exc or "").strip().lower()
            retryable_exception = any(
                token in err_text
                for token in (
                    "runtime_db_locked",
                    "database is locked",
                    "database table is locked",
                )
            )
            if retryable_exception:
                # Treat unexpected SQLite lock exceptions the same as structured
                # runtime_db_locked responses: keep pending and retry silently.
                self._notified_delayed_videos.add(_vid)
                if silent:
                    logger.debug(
                        "📺 Silent retryable dispatch exception video_id=%s error=%s",
                        _vid,
                        self._last_error,
                    )
                else:
                    logger.warning(
                        "📺 Retryable dispatch exception defer video_id=%s error=%s (no notification emitted)",
                        _vid,
                        self._last_error,
                    )
                return False
            logger.exception(
                "📺 Dispatch error video_id=%s: %s", item.get("video_id"), exc
            )
            self._emit_notification(
                kind="youtube_playlist_dispatch_failed",
                title="Tutorial Dispatch Error",
                message=f"{item.get('title') or item.get('video_id')}: {self._last_error}",
                severity="error",
                metadata={
                    "video_id": item.get("video_id", ""),
                    "reason": self._last_error,
                    "failure_class": "dispatch_exception",
                },
            )
            return False

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

    async def _process_new_video(
        self,
        item: dict[str, Any],
        seen: set[str],
        current_ids: list[str],
        playlist_id: str,
    ) -> bool:
        """Check-and-register a video then dispatch it. Returns True if dispatched.

        The critical section (inflight check/add, then seen update on success) is
        protected by _dispatch_lock so that concurrent calls from _loop and poll_now
        never both dispatch the same unseen video at the same time.

        The video is persisted to seen BEFORE dispatch so that restarts / manual
        polls never re-discover the same video and re-emit 'Dispatch Delayed'
        notifications.  If dispatch fails the video is also tracked in the
        in-memory _pending_dispatch_items for silent retry on the next poll.
        """
        vid = item["video_id"]
        already_processed = _video_has_processed_tutorial_artifacts(vid)
        async with self._dispatch_lock:
            if vid in seen:
                return False
            if vid in self._inflight_dispatches:
                return False
            if already_processed:
                seen.add(vid)
                self._persist_seen_state(seen, current_ids, timestamp_key="updated_at")
                self._pending_dispatch_items.pop(vid, None)
                self._notified_delayed_videos.discard(vid)
                logger.info(
                    "📺 Skipping redispatch for already-processed tutorial video_id=%s",
                    vid,
                )
                return False
            self._inflight_dispatches.add(vid)
            # ── Persist to seen immediately so restarts/manual polls
            #    never re-discover this video.  If dispatch subsequently
            #    fails, the video stays seen (no re-notification) and is
            #    tracked in _pending_dispatch_items for silent retry.
            seen.add(vid)
            self._persist_seen_state(seen, current_ids, timestamp_key="updated_at")

        # Dispatch outside the lock (slow operation). We intentionally do not
        # emit detection-only notifications here; user-facing updates begin
        # when the run is admitted.
        logger.info(
            "📺 New playlist video detected video_id=%s title=%r",
            vid,
            item.get("title", ""),
        )
        accepted = False
        try:
            accepted = await self._dispatch(item)
        except Exception as exc:
            # _dispatch normally swallows/records its own errors, but keep this
            # outer guard so an unexpected monkeypatch/runtime error never marks
            # the item as seen or kills the whole poll cycle.
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "📺 Unexpected watcher dispatch wrapper error video_id=%s: %s",
                vid,
                exc,
            )
            self._emit_notification(
                kind="youtube_playlist_dispatch_failed",
                title="Tutorial Dispatch Error",
                message=f"{item.get('title') or vid}: {self._last_error}",
                severity="error",
                metadata={
                    "video_id": vid,
                    "reason": self._last_error,
                    "failure_class": "dispatch_wrapper_exception",
                },
            )
        finally:
            async with self._dispatch_lock:
                self._inflight_dispatches.discard(vid)
                if accepted:
                    self._dispatched_total += 1
                    self._pending_dispatch_items.pop(vid, None)
                    # Record dispatch timestamp for post-dispatch monitoring
                    if vid not in self._dispatch_timestamps:
                        self._dispatch_timestamps[vid] = time.time()
                else:
                    # Track for silent retry on next poll — no new notification
                    self._pending_dispatch_items[vid] = item
                    # Record dispatch timestamp if this is the first attempt
                    if vid not in self._dispatch_timestamps:
                        self._dispatch_timestamps[vid] = time.time()
                self._persist_seen_state(seen, current_ids, timestamp_key="updated_at")
        return accepted


    async def poll_now(self) -> dict[str, Any]:
        """Manually trigger one poll cycle. Returns result summary."""
        playlist_id = os.getenv("YT_TUTORIALS_PLAYLIST_ID", "").strip()
        api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
        if not playlist_id:
            return {"ok": False, "reason": "missing_config"}
        state = _load_state()
        seen: set[str] = set(state.get("seen_ids", []))
        items = await self._fetch_playlist_items(playlist_id, api_key)
        if items is None:
            return {"ok": False, "reason": self._last_error or "fetch_failed"}
        self._last_poll_at = _iso_now()
        self._poll_count += 1
        self._last_poll_ok = True
        self._last_error = ""
        current_ids = [i["video_id"] for i in items if i.get("video_id")]
        # Merge watcher's in-memory seen set + pending items so manual polls
        # never re-discover videos already tracked by the background loop.
        seen.update(self._seen_ids)
        if (
            self._task is not None
            and not self._task.done()
            and self._poll_count == 1
            and not seen
            and not self._pending_dispatch_items
        ):
            seen.update(current_ids)
            self._seen_ids = set(seen)
            self._persist_seen_state(seen, current_ids, timestamp_key="seeded_at")
            logger.info(
                "📺 poll_now: startup seed applied playlist_id=%s seen=%d",
                playlist_id,
                len(seen),
            )
            return {
                "ok": True,
                "total_in_playlist": len(items),
                "new_dispatched": 0,
                "dispatched_video_ids": [],
                "seeded_existing": len(current_ids),
                "polled_at": self._last_poll_at,
            }
        dispatched = []
        for vid in list(self._pending_dispatch_items):
            pending_item = self._pending_dispatch_items[vid]
            if _video_has_processed_tutorial_artifacts(vid):
                async with self._dispatch_lock:
                    seen.add(vid)
                    self._persist_seen_state(seen, current_ids, timestamp_key="updated_at")
                self._pending_dispatch_items.pop(vid, None)
                self._notified_delayed_videos.discard(vid)
                logger.info(
                    "📺 poll_now: cleared stale pending dispatch for already-processed video_id=%s",
                    vid,
                )
                continue
            logger.info("📺 poll_now: retrying pending dispatch video_id=%s", vid)
            ok = await self._dispatch(pending_item, silent=True)
            if ok:
                async with self._dispatch_lock:
                    seen.add(vid)
                    self._persist_seen_state(seen, current_ids, timestamp_key="updated_at")
                    self._dispatched_total += 1
                self._pending_dispatch_items.pop(vid, None)
                self._notified_delayed_videos.discard(vid)
                dispatched.append(vid)
                logger.info("📺 poll_now: pending dispatch succeeded video_id=%s", vid)
        new_items = [
            i for i in items
            if i.get("video_id")
            and i["video_id"] not in seen
            and i["video_id"] not in self._pending_dispatch_items
        ]
        for item in reversed(new_items):
            vid = item["video_id"]
            ok = await self._process_new_video(item, seen, current_ids, playlist_id)
            if ok:
                dispatched.append(vid)
            else:
                logger.info(
                    "📺 poll_now: skipping duplicate dispatch video_id=%s (already dispatched this session)",
                    vid,
                )
        return {
            "ok": True,
            "total_in_playlist": len(items),
            "new_dispatched": len(dispatched),
            "dispatched_video_ids": dispatched,
            "polled_at": self._last_poll_at,
        }
