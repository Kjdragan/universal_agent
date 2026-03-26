"""Idle Agent Dispatch Loop.

A lightweight background task that continuously checks if agents are idle
and the task queue has work waiting. When an idle agent is detected and
tasks exist, it wakes the heartbeat to dispatch.

This decouples task execution from the fixed ~30min heartbeat interval,
enabling agents to grab work as soon as they're free.
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


async def idle_dispatch_loop(
    *,
    heartbeat_service: Any,
    get_sessions_fn: Any,
    notification_sink: Optional[Any] = None,
    get_heartbeat_sessions_fn: Optional[Any] = None,
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
    if not IDLE_POLL_ENABLED:
        logger.info("🔄 Idle dispatch loop disabled (UA_IDLE_POLL_ENABLED=0)")
        return

    logger.info(
        "🔄 Idle dispatch loop started (interval=%ds)",
        IDLE_POLL_INTERVAL_SECONDS,
    )

    consecutive_errors = 0
    last_dispatch_time = 0.0

    while True:
        try:
            await asyncio.sleep(IDLE_POLL_INTERVAL_SECONDS)

            if not heartbeat_service:
                continue

            # 1. Find all sessions — prefer gateway sessions, fall back to
            #    heartbeat-registered sessions (includes daemon sessions)
            sessions = get_sessions_fn()
            if not sessions and get_heartbeat_sessions_fn:
                sessions = get_heartbeat_sessions_fn()

            if not sessions:
                continue

            # 2. Find idle agents (not in busy_sessions set)
            busy = heartbeat_service.busy_sessions or set()
            idle_sessions = [
                sid for sid in sessions.keys()
                if sid not in busy
            ]

            if not idle_sessions:
                continue  # All agents busy — nothing to do

            # 3. Check if the task queue has pending work
            #    We use the heartbeat's own task scanning logic by simply
            #    requesting a heartbeat run. The heartbeat itself checks
            #    Task Hub, etc. and only dispatches if there's work.
            #    This is intentionally simple — we don't duplicate task scanning.
            now = time.time()

            # Rate-limit: don't dispatch idle wakes more than once per interval
            if now - last_dispatch_time < IDLE_POLL_INTERVAL_SECONDS:
                continue

            # Wake ONE idle session (round-robin would add complexity
            # for minimal benefit — the heartbeat handles multi-session)
            target_sid = sorted(idle_sessions)[0]

            heartbeat_service.request_heartbeat_now(
                target_sid, reason="idle_dispatch_poll"
            )
            last_dispatch_time = now

            logger.info(
                "🔄 Idle dispatch: woke session=%s (idle=%d, busy=%d)",
                target_sid,
                len(idle_sessions),
                len(busy),
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

