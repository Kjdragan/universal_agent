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
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

_YOUTUBE_API_PLAYLIST_ITEMS = "https://www.googleapis.com/youtube/v3/playlistItems"
_YOUTUBE_RSS_FEED = "https://www.youtube.com/feeds/videos.xml"
_STATE_FILENAME = "youtube_playlist_watcher_state.json"
MODE_EXPLAINER_ONLY = "explainer_only"
MODE_EXPLAINER_PLUS_CODE = "explainer_plus_code"
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
_CODE_HINT_KEYWORDS = {
    "code",
    "coding",
    "programming",
    "python",
    "javascript",
    "typescript",
    "react",
    "nextjs",
    "next.js",
    "mcp",
    "api",
    "sdk",
    "cli",
    "sql",
    "database",
    "docker",
    "kubernetes",
    "repo",
    "github",
    "automation",
    "agent",
}
_NON_CODE_HINT_KEYWORDS = {
    "recipe",
    "cooking",
    "cook",
    "food",
    "kitchen",
    "grill",
    "charcoal",
    "souvlaki",
    "baking",
    "travel",
    "vlog",
    "music",
    "song",
    "workout",
    "fitness",
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


def _infer_youtube_mode(*parts: Any) -> str:
    tokens = " ".join(str(part or "") for part in parts).strip().lower()
    if not tokens:
        return MODE_EXPLAINER_ONLY
    has_code = any(keyword in tokens for keyword in _CODE_HINT_KEYWORDS)
    has_non_code = any(keyword in tokens for keyword in _NON_CODE_HINT_KEYWORDS)
    if has_non_code and not has_code:
        return MODE_EXPLAINER_ONLY
    return MODE_EXPLAINER_PLUS_CODE if has_code else MODE_EXPLAINER_ONLY


DispatchResult = tuple[bool, str] | dict[str, Any]
DispatchFn = Callable[[str, dict[str, Any]], Coroutine[Any, Any, DispatchResult]]
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
        }

    def _persist_seen_state(
        self,
        seen: set[str],
        current_ids: list[str],
        *,
        timestamp_key: str,
    ) -> set[str]:
        if len(seen) > 1000:
            seen = set(current_ids[:1000])
        self._seen_count = len(seen)
        _save_state({"seen_ids": list(seen), timestamp_key: _iso_now()})
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
                    if vid in seen:
                        self._pending_dispatch_items.pop(vid, None)
                        continue
                    pending_item = self._pending_dispatch_items[vid]
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
        mode = _infer_youtube_mode(
            item.get("title"),
            item.get("channel_id"),
            item.get("url"),
            item.get("playlist_id"),
        )
        payload = {
            "video_url": item.get("url", f"https://www.youtube.com/watch?v={item['video_id']}"),
            "video_id": item.get("video_id", ""),
            "channel_id": item.get("channel_id", ""),
            "title": item.get("title", ""),
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
                # In silent mode (pending retry), skip ALL notifications
                if not silent:
                    # Suppress duplicate delayed notifications (only one per video)
                    if _is_retryable and _vid in self._notified_delayed_videos:
                        logger.debug(
                            "📺 Suppressed duplicate Dispatch Delayed for video_id=%s", _vid,
                        )
                    else:
                        if _is_retryable:
                            self._notified_delayed_videos.add(_vid)
                        self._emit_notification(
                            kind="youtube_playlist_dispatch_failed",
                            title=(
                                "Tutorial Dispatch Delayed" if _is_retryable
                                else "Tutorial Dispatch Rejected"
                            ),
                            message=(
                                f"{item.get('title') or _vid}: "
                                "runtime storage is temporarily busy; automatic retry will occur on the next playlist poll."
                                if _is_retryable
                                else f"{item.get('title') or _vid}: {reason}"
                            ),
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
                    logger.debug("📺 Silent retry failed for video_id=%s reason=%s", _vid, reason)
                return False
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
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
        async with self._dispatch_lock:
            if vid in seen:
                return False
            if vid in self._inflight_dispatches:
                return False
            self._inflight_dispatches.add(vid)
            # ── Persist to seen immediately so restarts/manual polls
            #    never re-discover this video.  If dispatch subsequently
            #    fails, the video stays seen (no re-notification) and is
            #    tracked in _pending_dispatch_items for silent retry.
            seen.add(vid)
            self._persist_seen_state(seen, current_ids, timestamp_key="updated_at")

        # Emit notification and dispatch outside the lock (slow operations).
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
                else:
                    # Track for silent retry on next poll — no new notification
                    self._pending_dispatch_items[vid] = item
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
        new_items = [
            i for i in items
            if i.get("video_id")
            and i["video_id"] not in seen
            and i["video_id"] not in self._pending_dispatch_items
        ]
        dispatched = []
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
