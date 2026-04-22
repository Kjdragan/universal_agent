"""Idle Agent Dispatch Loop.

A lightweight background task that continuously checks if agents are idle
and the task queue has work waiting. When an idle agent is detected and
tasks exist, it wakes the heartbeat to dispatch.

This decouples task execution from the fixed ~30min heartbeat interval,
enabling agents to grab work as soon as they're free.

Phase 3 Enhancement: asyncio.Event nudge mechanism.
External callers (e.g. email hooks) can call ``nudge_dispatch()`` to
wake the loop immediately instead of waiting for the next poll interval.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

IDLE_POLL_INTERVAL_SECONDS = int(os.getenv("UA_IDLE_POLL_INTERVAL_SECONDS", "60"))
IDLE_POLL_ENABLED = str(os.getenv("UA_IDLE_POLL_ENABLED", "1")).strip().lower() in {
    "1", "true", "yes", "on",
}

# ── Nudge Mechanism ─────────────────────────────────────────────────────────
# Phase 3: asyncio.Event that can be set by external callers to wake the
# idle dispatch loop immediately.  Thread-safe via loop.call_soon_threadsafe.

_nudge_event: Optional[asyncio.Event] = None
_nudge_loop: Optional[asyncio.AbstractEventLoop] = None


def nudge_dispatch(reason: str = "external") -> None:
    """Signal the idle dispatch loop to wake immediately.

    Safe to call from any thread or coroutine.  If the event loop is
    not yet running (server still starting), the nudge is silently
    dropped — the next poll will pick up the work anyway.

    Parameters
    ----------
    reason : str
        Human-readable reason for the nudge (logged for observability).
    """
    global _nudge_event, _nudge_loop
    if _nudge_event is None:
        logger.debug("nudge_dispatch(%s): no event registered yet, ignoring", reason)
        return

    logger.info("⚡ nudge_dispatch: waking idle loop (%s)", reason)

    # If we're already on the event loop, just set it directly.
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is not None and running_loop is _nudge_loop:
        _nudge_event.set()
    elif _nudge_loop is not None:
        # Cross-thread: schedule on the correct loop
        _nudge_loop.call_soon_threadsafe(_nudge_event.set)
    else:
        # Fallback: set directly and hope for the best
        _nudge_event.set()


async def idle_dispatch_loop(
    *,
    heartbeat_service: Any,
    get_sessions_fn: Any,
    notification_sink: Optional[Any] = None,
    get_heartbeat_sessions_fn: Optional[Any] = None,
    todo_dispatch_service: Optional[Any] = None,
) -> None:
    """Background loop: if agents are idle and tasks exist, dispatch.

    Parameters
    ----------
    heartbeat_service : HeartbeatService
        The singleton heartbeat service for waking sessions.
    get_sessions_fn : callable
        Returns dict of active sessions {session_id: session_obj}.
    notification_sink : callable, optional
        For emitting notifications to the dashboard.
    get_heartbeat_sessions_fn : callable, optional
        Returns dict of heartbeat-registered sessions (including daemon sessions).
        Used as a fallback when no WebSocket sessions exist — ensures daemon
        sessions are discoverable by the idle dispatch loop.
    """
    global _nudge_event, _nudge_loop

    if not IDLE_POLL_ENABLED:
        logger.info("🔄 Idle dispatch loop disabled (UA_IDLE_POLL_ENABLED=0)")
        return

    # Initialize the nudge event on the running loop
    _nudge_event = asyncio.Event()
    _nudge_loop = asyncio.get_running_loop()

    logger.info(
        "🔄 Idle dispatch loop started (interval=%ds, nudge=enabled)",
        IDLE_POLL_INTERVAL_SECONDS,
    )

    consecutive_errors = 0
    last_dispatch_time = 0.0

    while True:
        try:
            # Phase 3: Wait for either the nudge event OR the poll interval.
            # If nudge_dispatch() is called, we wake immediately.
            nudge_reason = "poll"
            try:
                await asyncio.wait_for(
                    _nudge_event.wait(),
                    timeout=IDLE_POLL_INTERVAL_SECONDS,
                )
                # Nudge received — clear the event for next time
                _nudge_event.clear()
                nudge_reason = "nudge"
            except asyncio.TimeoutError:
                pass  # Normal poll interval elapsed

            if not heartbeat_service:
                continue

            # 1. Find candidate sessions.
            #    When the dedicated ToDo dispatcher is available, only use its
            #    registered execution sessions. Otherwise fall back to the
            #    legacy heartbeat-oriented session discovery.
            if todo_dispatch_service:
                sessions = dict(getattr(todo_dispatch_service, "active_sessions", {}) or {})
            else:
                sessions = get_sessions_fn()
                if not sessions and get_heartbeat_sessions_fn:
                    sessions = get_heartbeat_sessions_fn()

            if not sessions:
                continue

            # 2. Find idle agents using the canonical executor's busy state
            busy = (
                getattr(todo_dispatch_service, "busy_sessions", set()) or set()
                if todo_dispatch_service
                else (heartbeat_service.busy_sessions or set())
            )
            # Also consider sessions with active execution tasks as busy.
            # The ToDo dispatch _process_session returns immediately after
            # queueing work, but the actual execution runs asynchronously.
            if todo_dispatch_service:
                executing = getattr(todo_dispatch_service, "executing_sessions", set()) or set()
                if executing:
                    busy = busy | executing
            idle_sessions = [
                sid for sid in sessions.keys()
                if sid not in busy
            ]

            if not idle_sessions:
                if nudge_reason == "nudge":
                    logger.info(
                        "⚡ Nudge received but all agents busy — task queued for next idle window"
                    )
                continue  # All agents busy — nothing to do

            # 3. Check if the task queue has pending work
            #    We use the heartbeat's own task scanning logic by simply
            #    requesting a heartbeat run. The heartbeat itself checks
            #    Task Hub, etc. and only dispatches if there's work.
            #    This is intentionally simple — we don't duplicate task scanning.
            now = time.time()

            # Rate-limit: for nudges, allow more aggressive dispatch (10s cooldown).
            # For regular polls, maintain the full interval cooldown.
            cooldown = 10 if nudge_reason == "nudge" else IDLE_POLL_INTERVAL_SECONDS
            if now - last_dispatch_time < cooldown:
                continue

            # Wake ONE idle session (round-robin would add complexity
            # for minimal benefit — the heartbeat handles multi-session)
            target_sid = sorted(idle_sessions)[0]

            if todo_dispatch_service:
                todo_dispatch_service.request_dispatch_now(target_sid)
            else:
                heartbeat_service.request_heartbeat_now(
                    target_sid, reason=f"idle_dispatch_{nudge_reason}"
                )
            
            last_dispatch_time = now

            logger.info(
                "🔄 Idle dispatch: woke session=%s (idle=%d, busy=%d, trigger=%s)",
                target_sid,
                len(idle_sessions),
                len(busy),
                nudge_reason,
            )

            consecutive_errors = 0

        except asyncio.CancelledError:
            logger.info("🔄 Idle dispatch loop cancelled")
            break
        except Exception:
            consecutive_errors += 1
            if consecutive_errors <= 3:
                logger.exception("🔄 Idle dispatch loop error (attempt %d)", consecutive_errors)
            # Back off on repeated errors
            await asyncio.sleep(min(300, IDLE_POLL_INTERVAL_SECONDS * consecutive_errors))
