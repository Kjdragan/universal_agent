"""Google Workspace Event Listener (Phase 5).

Polls the Gmail API for new messages via the `gws` CLI and dispatches
them into the UA hooks pipeline for downstream agent processing.

Feature-gated by UA_ENABLE_GOOGLE_WORKSPACE_EVENTS (default: 0 / OFF).
Requires UA_ENABLE_GWS_CLI=1 and valid gws auth credentials.

Env vars (all optional with safe defaults):
    UA_ENABLE_GOOGLE_WORKSPACE_EVENTS   — "1" to activate (default: "0")
    UA_DISABLE_GOOGLE_WORKSPACE_EVENTS  — "1" to hard-disable
    UA_GWS_EVENTS_POLL_INTERVAL_SECONDS — poll cadence in seconds (default: 60)
    UA_GWS_EVENTS_GMAIL_LABELS          — comma-separated Gmail label IDs (default: "INBOX,UNREAD")
    UA_GWS_EVENTS_MAX_RESULTS           — max messages per poll (default: 20)
    UA_GWS_EVENTS_DISPATCH_HOOK         — hook subpath for dispatch (default: "gmail/new_message")
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable, Coroutine, Optional

from universal_agent.feature_flags import gws_events_enabled

logger = logging.getLogger(__name__)

_STATE_FILENAME = "gws_event_listener_state.json"

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _poll_interval() -> float:
    try:
        return max(15.0, float(os.getenv("UA_GWS_EVENTS_POLL_INTERVAL_SECONDS", "60")))
    except Exception:
        return 60.0


def _gmail_labels() -> str:
    return os.getenv("UA_GWS_EVENTS_GMAIL_LABELS", "INBOX,UNREAD").strip() or "INBOX,UNREAD"


def _max_results() -> int:
    try:
        return max(1, int(os.getenv("UA_GWS_EVENTS_MAX_RESULTS", "20")))
    except Exception:
        return 20


def _dispatch_hook() -> str:
    return os.getenv("UA_GWS_EVENTS_DISPATCH_HOOK", "gmail/new_message").strip() or "gmail/new_message"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

DispatchFn = Callable[[str, dict[str, Any]], Coroutine[Any, Any, tuple[bool, str]]]
NotifyFn = Callable[[dict[str, Any]], None]


# ---------------------------------------------------------------------------
# Core service
# ---------------------------------------------------------------------------


class GwsEventListener:
    """Async polling service that watches Gmail for new messages via the gws CLI.

    On each poll, it calls `gws gmail users messages list` with the configured
    label filter, detects newly-arrived message IDs, fetches their metadata,
    and dispatches them into the UA hooks pipeline.

    Lifecycle follows the same start/stop pattern as YouTubePlaylistWatcher
    so it integrates cleanly with gateway_server.py.
    """

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

        self._enabled = gws_events_enabled()
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
            logger.info(
                "📬 gws event listener DISABLED "
                "(set UA_ENABLE_GOOGLE_WORKSPACE_EVENTS=1 to activate)"
            )
            return

        if not shutil.which("gws"):
            logger.warning(
                "📬 gws event listener: binary not found on $PATH — not starting"
            )
            self._enabled = False
            return

        state = _load_state()
        seen: set[str] = set(state.get("seen_ids", []))
        self._seen_count = len(seen)

        logger.info(
            "📬 gws event listener started labels=%s poll_interval=%.0fs",
            _gmail_labels(),
            _poll_interval(),
        )
        self._task = asyncio.create_task(self._loop(seen))

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
        return {
            "enabled": self._enabled,
            "gmail_labels": _gmail_labels(),
            "poll_interval_seconds": _poll_interval(),
            "max_results": _max_results(),
            "dispatch_hook": _dispatch_hook(),
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

    async def _loop(self, seen: set[str]) -> None:
        seeded = False
        while not self._stop_event.is_set():
            try:
                message_ids = await self._list_message_ids()
                self._last_poll_at = _iso_now()
                self._poll_count += 1

                if message_ids is None:
                    self._last_poll_ok = False
                    await self._sleep_or_stop(_poll_interval())
                    continue

                if not seeded:
                    seen.update(message_ids)
                    seeded = True
                    self._seen_count = len(seen)
                    self._last_poll_ok = True
                    logger.info(
                        "📬 gws event listener seeded labels=%s seen=%d",
                        _gmail_labels(),
                        len(seen),
                    )
                    _save_state({"seen_ids": list(seen), "seeded_at": _iso_now()})
                    await self._sleep_or_stop(_poll_interval())
                    continue

                new_ids = [mid for mid in message_ids if mid not in seen]
                self._last_poll_ok = True
                self._last_error = ""

                for message_id in reversed(new_ids):
                    seen.add(message_id)
                    self._seen_count = len(seen)
                    self._dispatched_total += 1
                    logger.info("📬 New Gmail message detected id=%s", message_id)

                    metadata = await self._fetch_message_metadata(message_id)
                    self._emit_notification(
                        kind="gws_gmail_new_message",
                        title="New Gmail Message",
                        message=f"{metadata.get('subject', '(no subject)')} from {metadata.get('from_', '')}",
                        severity="info",
                        metadata={"message_id": message_id, **metadata},
                    )
                    await self._dispatch(message_id, metadata)

                # Trim seen to prevent unbounded growth
                if len(seen) > 2000:
                    seen = set(list(seen)[-2000:])
                _save_state({"seen_ids": list(seen), "updated_at": _iso_now()})

            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._last_poll_ok = False
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._last_poll_at = _iso_now()
                logger.exception("📬 gws event listener poll error: %s", exc)

            await self._sleep_or_stop(_poll_interval())

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # ------------------------------------------------------------------
    # gws CLI calls (run in executor to avoid blocking event loop)
    # ------------------------------------------------------------------

    async def _list_message_ids(self) -> Optional[list[str]]:
        """Call `gws gmail users messages list` and return message IDs."""
        params: dict[str, Any] = {
            "userId": "me",
            "maxResults": _max_results(),
            "labelIds": _gmail_labels().split(","),
        }
        cmd = ["gws", "gmail", "users", "messages", "list",
               "--params", json.dumps(params)]
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=20,
                ),
            )
            if result.returncode != 0:
                err_text = (result.stderr or result.stdout or "").strip()
                try:
                    err_data = json.loads(err_text)
                    error_msg = err_data.get("error", {}).get("message", err_text[:200])
                except Exception:
                    error_msg = err_text[:200]
                logger.warning("📬 gws messages list failed: %s", error_msg)
                self._last_error = error_msg
                return None

            data = json.loads(result.stdout)
            messages = data.get("messages") or []
            return [m["id"] for m in messages if m.get("id")]

        except subprocess.TimeoutExpired:
            logger.warning("📬 gws messages list timed out")
            self._last_error = "timeout"
            return None
        except Exception as exc:
            logger.warning("📬 gws messages list error: %s", exc)
            self._last_error = f"{type(exc).__name__}: {exc}"
            return None

    async def _fetch_message_metadata(self, message_id: str) -> dict[str, Any]:
        """Fetch subject/from/date for a message ID. Returns empty dict on failure."""
        params = {"userId": "me", "id": message_id, "format": "metadata",
                  "metadataHeaders": ["Subject", "From", "Date"]}
        cmd = ["gws", "gmail", "users", "messages", "get",
               "--params", json.dumps(params)]
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=15),
            )
            if result.returncode != 0:
                return {"message_id": message_id}
            data = json.loads(result.stdout)
            headers = {
                h["name"].lower(): h.get("value", "")
                for h in (data.get("payload", {}).get("headers") or [])
            }
            return {
                "message_id": message_id,
                "subject": headers.get("subject", "(no subject)"),
                "from_": headers.get("from", ""),
                "date": headers.get("date", ""),
                "thread_id": data.get("threadId", ""),
                "label_ids": data.get("labelIds", []),
                "snippet": data.get("snippet", ""),
            }
        except Exception:
            return {"message_id": message_id}

    # ------------------------------------------------------------------
    # Dispatch + notification helpers
    # ------------------------------------------------------------------

    async def _dispatch(self, message_id: str, metadata: dict[str, Any]) -> None:
        payload = {
            "message_id": message_id,
            "subject": metadata.get("subject", ""),
            "from_": metadata.get("from_", ""),
            "date": metadata.get("date", ""),
            "thread_id": metadata.get("thread_id", ""),
            "label_ids": metadata.get("label_ids", []),
            "snippet": metadata.get("snippet", ""),
            "source": "gws_event_listener",
        }
        try:
            ok, reason = await self._dispatch_fn(_dispatch_hook(), payload)
            if ok:
                logger.info("📬 Dispatched Gmail message id=%s", message_id)
            else:
                logger.warning(
                    "📬 Dispatch rejected message_id=%s reason=%s", message_id, reason
                )
        except Exception as exc:
            logger.exception("📬 Dispatch error message_id=%s: %s", message_id, exc)

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
            logger.exception("📬 Failed emitting notification kind=%s", kind)

    # ------------------------------------------------------------------
    # Manual poll (for ops endpoint)
    # ------------------------------------------------------------------

    async def poll_now(self) -> dict[str, Any]:
        """Manually trigger one poll cycle. Returns result summary."""
        if not self._enabled:
            return {"ok": False, "reason": "disabled"}
        if not shutil.which("gws"):
            return {"ok": False, "reason": "gws_binary_not_found"}

        state = _load_state()
        seen: set[str] = set(state.get("seen_ids", []))

        message_ids = await self._list_message_ids()
        if message_ids is None:
            return {"ok": False, "reason": self._last_error or "list_failed"}

        self._last_poll_at = _iso_now()
        self._poll_count += 1
        self._last_poll_ok = True

        new_ids = [mid for mid in message_ids if mid not in seen]
        dispatched = []
        for message_id in reversed(new_ids):
            seen.add(message_id)
            self._seen_count = len(seen)
            self._dispatched_total += 1
            metadata = await self._fetch_message_metadata(message_id)
            self._emit_notification(
                kind="gws_gmail_new_message",
                title="New Gmail Message",
                message=f"{metadata.get('subject', '(no subject)')} from {metadata.get('from_', '')}",
                severity="info",
                metadata={"message_id": message_id, **metadata},
            )
            await self._dispatch(message_id, metadata)
            dispatched.append(message_id)

        _save_state({"seen_ids": list(seen), "updated_at": _iso_now()})
        return {
            "ok": True,
            "total_found": len(message_ids),
            "new_dispatched": len(dispatched),
            "dispatched_message_ids": dispatched,
            "polled_at": self._last_poll_at,
        }
