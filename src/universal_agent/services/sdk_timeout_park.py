"""Park SDK-path VP missions after N consecutive wall-clock timeouts.

Context
-------
VP missions executed through ``ProcessTurnAdapter`` (the Claude Agent SDK
path in ``claude_code_client.py`` / ``claude_generalist_client.py``) are
subject to a wall-clock cap enforced in ``execution_engine.py``. When the
cap fires (``ProcessTurnAdapter timed out after Xs``) the adapter
cancels the engine task but the SDK's bundled ``claude`` subprocess
and its MCP children are not always reaped cleanly. Over the lifetime of
one worker run, the resulting orphan PIDs / memory drift trip the systemd
cgroup limits (``pids.max=256`` on this deployment) and the worker dies.
``Restart=always`` brings a new worker up; the dispatch sweep re-claims
the same mission; the cycle repeats — observed in production at
~6m44s per cycle, hammering the same ``vp-mission-<id>`` indefinitely.

This module breaks the loop by gating on **consecutive** timeouts for a
given Task Hub task. The counter lives in ``task_hub_items.metadata_json``
under ``sdk_consecutive_timeouts`` so it survives worker restarts. On the
Nth consecutive timeout (default 3, override via
``UA_SDK_TIMEOUT_POISON_THRESHOLD``) the task is parked into
``needs_review`` via ``perform_task_action(action="review", ...)`` and the
dispatch sweep stops re-claiming it. A non-timeout outcome on the same
task resets the counter.

Design constraints
------------------
* **Best-effort.** All public helpers swallow exceptions and log at
  ``debug``. They must never raise into the mission code path — SDK
  timeout is already an error route; a secondary failure here must not
  obscure or replace the original failure.
* **No new schema.** Reuses the existing ``metadata_json`` JSON blob on
  ``task_hub_items`` (per the established pattern in ``perform_task_action``
  for ``last_reject_reason``, etc.).
* **Shared by both SDK clients.** ``ClaudeCodeClient`` (vp.coder.primary)
  and ``ClaudeGeneralistClient`` (vp.general.primary) both call into
  these helpers — the CLI path (``ClaudeCodeCLIClient``) uses a different
  mechanism (the F.1/F.3 ``classify_worker_exit`` flow in
  ``claude_cli_client._classify_and_route_cli_exit``).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

# Phrases that indicate the ProcessTurnAdapter wall-clock cap fired.
# Match the ERROR event emitted in ``execution_engine.py`` around
# ``ProcessTurnAdapter timed out after %.1fs`` — the message field on the
# yielded ERROR event reads ``"Execution timed out after {N}s"``.
_SDK_TIMEOUT_MARKERS: tuple[str, ...] = (
    "execution timed out after",
    "processturnadapter timed out after",
)

_DEFAULT_THRESHOLD = 3
_COUNTER_KEY = "sdk_consecutive_timeouts"
_LAST_REASON_KEY = "sdk_last_timeout_reason"

_PARK_REASON_PREFIX = "sdk_timeout_poison_pill"


def _resolve_threshold() -> int:
    raw = os.getenv("UA_SDK_TIMEOUT_POISON_THRESHOLD", "").strip()
    if not raw:
        return _DEFAULT_THRESHOLD
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "UA_SDK_TIMEOUT_POISON_THRESHOLD=%r is not an integer; using default %d",
            raw, _DEFAULT_THRESHOLD,
        )
        return _DEFAULT_THRESHOLD
    if value < 1:
        logger.warning(
            "UA_SDK_TIMEOUT_POISON_THRESHOLD=%d must be >=1; using default %d",
            value, _DEFAULT_THRESHOLD,
        )
        return _DEFAULT_THRESHOLD
    return value


def is_sdk_timeout(error_text: Optional[str]) -> bool:
    """Return True if ``error_text`` looks like a ProcessTurnAdapter timeout.

    Matches against the ``Execution timed out after ...`` message yielded
    by the SDK wall-clock cap path. Case-insensitive, substring match.
    """
    if not error_text:
        return False
    haystack = str(error_text).lower()
    return any(marker in haystack for marker in _SDK_TIMEOUT_MARKERS)


def record_sdk_timeout_and_maybe_park(
    *,
    task_id: str,
    mission_id: str,
    error_text: str,
    threshold: Optional[int] = None,
) -> tuple[bool, int]:
    """Record an SDK timeout for a task and park it if the threshold is reached.

    Args:
        task_id: Task Hub item id from the mission payload. Empty/missing
            ⇒ no-op (returns ``(False, 0)``).
        mission_id: VP mission id, used only for log grep-ability.
        error_text: The ERROR-event message from the SDK adapter. The
            caller should still pass this even if it's already certain
            it's a timeout — the helper double-checks via
            :func:`is_sdk_timeout` to avoid mis-incrementing on
            non-timeout failures.
        threshold: Override the env-driven default (mainly for tests).

    Returns:
        ``(parked, count)`` where ``count`` is the updated consecutive
        timeout count, and ``parked`` is True iff this call routed the
        task to ``needs_review``. Best-effort: returns ``(False, 0)`` on
        any DB error or if ``error_text`` isn't a timeout.
    """
    tid = str(task_id or "").strip()
    if not tid:
        return False, 0
    if not is_sdk_timeout(error_text):
        return False, 0

    limit = int(threshold) if threshold is not None else _resolve_threshold()

    try:
        from universal_agent import task_hub as _th
        from universal_agent.gateway_server import (
            _task_hub_open_conn as _open_conn,
        )
    except ImportError as exc:
        logger.debug(
            "SDK timeout parking imports failed for mission %s: %s",
            mission_id, exc,
        )
        return False, 0

    try:
        conn = _open_conn()
    except sqlite3.Error as exc:
        logger.debug(
            "SDK timeout parking: could not open task_hub conn for mission %s: %s",
            mission_id, exc,
        )
        return False, 0

    try:
        item = _th.get_item(conn, tid)
        if not item:
            logger.debug(
                "SDK timeout parking: task %s not found (mission %s)",
                tid, mission_id,
            )
            return False, 0

        metadata = dict(item.get("metadata") or {})
        current = int(metadata.get(_COUNTER_KEY) or 0)
        new_count = current + 1
        metadata[_COUNTER_KEY] = new_count
        # Store a short summary of the last timeout so the operator can
        # see *which* mission's failure tipped the parking decision
        # without needing to grep logs.
        metadata[_LAST_REASON_KEY] = (
            f"mission={mission_id} message={str(error_text)[:200]}"
        )

        if new_count >= limit:
            summary = (
                f"{new_count} consecutive SDK wall-clock timeouts "
                f"(last mission={mission_id})"
            )
            try:
                _th.perform_task_action(
                    conn,
                    task_id=tid,
                    action=_th.ACTION_REVIEW,
                    reason=f"{_PARK_REASON_PREFIX}: {summary}",
                    agent_id="sdk_timeout_park",
                )
                conn.commit()
                logger.error(
                    "SDK timeout parking: task %s parked to needs_review after "
                    "%d consecutive timeouts (mission %s)",
                    tid, new_count, mission_id,
                )
                return True, new_count
            except sqlite3.Error as exc:
                logger.warning(
                    "SDK timeout parking: perform_task_action(review) failed for "
                    "task %s (mission %s): %s",
                    tid, mission_id, exc,
                )
                return False, new_count

        # Below threshold — persist the bumped counter so the next
        # worker (after a restart) sees the accurate count.
        from universal_agent.task_hub import _json_dumps, _now_iso  # noqa: WPS433
        conn.execute(
            "UPDATE task_hub_items SET metadata_json=?, updated_at=? WHERE task_id=?",
            (_json_dumps(metadata), _now_iso(), tid),
        )
        conn.commit()
        logger.warning(
            "SDK timeout parking: task %s timeout %d/%d (mission %s)",
            tid, new_count, limit, mission_id,
        )
        return False, new_count
    except sqlite3.Error as exc:
        logger.debug(
            "SDK timeout parking: bookkeeping error for task %s (mission %s): %s",
            tid, mission_id, exc,
        )
        return False, 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def reset_sdk_timeout_counter(*, task_id: str, mission_id: str = "") -> None:
    """Clear the consecutive-timeout counter for a task.

    Called by SDK clients on any non-timeout outcome (successful
    completion OR a different failure mode) so that a transient timeout
    doesn't permanently count against a task that later runs fine.

    Best-effort. Silently returns on any error.
    """
    tid = str(task_id or "").strip()
    if not tid:
        return

    try:
        from universal_agent import task_hub as _th
        from universal_agent.gateway_server import (
            _task_hub_open_conn as _open_conn,
        )
    except ImportError as exc:
        logger.debug(
            "SDK timeout reset: imports failed for mission %s: %s",
            mission_id, exc,
        )
        return

    try:
        conn = _open_conn()
    except sqlite3.Error as exc:
        logger.debug(
            "SDK timeout reset: could not open task_hub conn (mission %s): %s",
            mission_id, exc,
        )
        return

    try:
        item = _th.get_item(conn, tid)
        if not item:
            return
        metadata = dict(item.get("metadata") or {})
        if _COUNTER_KEY not in metadata and _LAST_REASON_KEY not in metadata:
            return
        metadata.pop(_COUNTER_KEY, None)
        metadata.pop(_LAST_REASON_KEY, None)
        from universal_agent.task_hub import _json_dumps, _now_iso  # noqa: WPS433
        conn.execute(
            "UPDATE task_hub_items SET metadata_json=?, updated_at=? WHERE task_id=?",
            (_json_dumps(metadata), _now_iso(), tid),
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.debug(
            "SDK timeout reset: bookkeeping error for task %s (mission %s): %s",
            tid, mission_id, exc,
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


__all__ = [
    "is_sdk_timeout",
    "record_sdk_timeout_and_maybe_park",
    "reset_sdk_timeout_counter",
]
