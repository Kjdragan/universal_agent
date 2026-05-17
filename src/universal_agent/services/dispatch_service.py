"""Centralized dispatch service for the Task Hub.

Provides four entry points for triggering task dispatch:
  - dispatch_immediate: dashboard "Start Now" button
  - dispatch_on_approval: dashboard "Approve" button
  - dispatch_scheduled_due: timer-driven scheduled task dispatch
  - dispatch_sweep: drop-in replacement for heartbeat's inline claim

All dispatch paths enrich claimed tasks with Simone-first routing metadata.
Simone is the primary orchestrator and decides delegation via batch triage.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any, Iterable, Optional

from universal_agent import task_hub
from universal_agent.loop_control import should_run_loop

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stale-assignment release (Hermes-adaptation Phase A.2)
# ---------------------------------------------------------------------------


def _stale_sweep_enabled() -> bool:
    """Phase A.2 feature gate.

    Resolution: explicit ``UA_DISPATCH_STALE_SWEEP_ENABLED`` env wins; else
    ON in production, OFF in development (so local dev doesn't release
    assignments on a timer when the operator isn't around to notice).
    """
    return should_run_loop("dispatch_stale_sweep", prod_default=True)


def _stale_after_seconds() -> int:
    """Resolve the per-sweep stale-cutoff in seconds.

    Env: ``UA_DISPATCH_STALE_AFTER_SECONDS`` (default 1800 = 30 minutes).
    Floored at 60s — anything tighter than a minute risks releasing
    assignments mid-tick during normal heartbeat cadence. Falls back to
    1800 on parse errors (e.g., operator typo).
    """
    raw = (os.getenv("UA_DISPATCH_STALE_AFTER_SECONDS") or "1800").strip()
    try:
        return max(60, int(raw))
    except ValueError:
        return 1800


def _release_stale_for_sweep(
    conn: sqlite3.Connection,
    *,
    provider_session_id: Optional[str],
    additional_running_sessions: Optional[Iterable[str]] = None,
    agent_id_prefix: Any = ("heartbeat:", "todo:"),
) -> None:
    """Top-of-sweep stale-assignment release. Hermes-adaptation Phase A.2.

    Mirrors Hermes' ``release_stale_claims`` call at the top of each
    ``dispatch_once`` tick (``kanban_db.py:3658``). Mode of operation:

    * Reads ``UA_DISPATCH_STALE_SWEEP_ENABLED`` (default on) and
      ``UA_DISPATCH_STALE_AFTER_SECONDS`` (default 1800).
    * Builds an exclude set from ``provider_session_id`` (the current caller)
      plus any ``additional_running_sessions`` caller provides. This protects
      live sessions from accidental release when a heartbeat tick takes
      longer than the stale-cutoff.
    * Calls ``task_hub.release_stale_assignments`` with the exclude set.
    * Logs the outcome for ops visibility; never raises into the caller.

    Best-effort — failures here must not block the actual claim that follows.
    """
    if not _stale_sweep_enabled():
        return
    excluded: set[str] = set()
    if provider_session_id:
        text = str(provider_session_id).strip()
        if text:
            excluded.add(text)
    if additional_running_sessions:
        try:
            for v in additional_running_sessions:
                t = str(v).strip()
                if t:
                    excluded.add(t)
        except TypeError:
            pass
    try:
        result = task_hub.release_stale_assignments(
            conn,
            agent_id_prefix=agent_id_prefix,
            stale_after_seconds=_stale_after_seconds(),
            exclude_session_ids=excluded if excluded else None,
        )
    except Exception as exc:  # noqa: BLE001 — never block the sweep
        log.warning("dispatch_sweep: stale-release pass failed: %s", exc)
        return
    if result.get("stale_detected") or result.get("skipped_live"):
        log.info(
            "dispatch_sweep stale-release: detected=%s finalized=%s reopened=%s skipped_live=%s",
            result.get("stale_detected", 0),
            result.get("finalized", 0),
            result.get("reopened", 0),
            result.get("skipped_live", 0),
        )


class DispatchError(Exception):
    """Raised when a dispatch operation cannot be completed."""


# ---------------------------------------------------------------------------
# Routing enrichment (applies to all dispatch paths)
# ---------------------------------------------------------------------------

def _enrich_with_routing(claimed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich claimed tasks with Simone-first routing metadata.

    Each claimed dict gets a '_routing' key with:
      - agent_id: always "simone" (Simone-first orchestration)
      - confidence: "orchestrator"
      - reason: human-readable justification
      - should_delegate: False (Simone decides delegation herself)

    Safe to call unconditionally — gracefully no-ops on import failure.
    """
    if not claimed:
        return claimed
    try:
        from universal_agent.services.agent_router import route_all_to_simone
        route_all_to_simone(claimed)
    except Exception as exc:
        log.debug("Agent routing enrichment unavailable: %s", exc)
    return claimed


# ---------------------------------------------------------------------------
# Immediate dispatch — dashboard "Start Now"
# ---------------------------------------------------------------------------

def dispatch_immediate(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    agent_id: str = "dashboard",
) -> dict[str, Any]:
    """Set a task to trigger_type='immediate', rebuild queue, and claim it.

    Returns the claimed assignment dict on success.
    Raises DispatchError if the task doesn't exist or can't be claimed.
    """
    item = task_hub.get_item(conn, task_id)
    if not item:
        raise DispatchError(f"Task {task_id!r} not found")

    current_status = str(item.get("status") or "").lower()
    if current_status not in (task_hub.TASK_STATUS_OPEN, task_hub.TASK_STATUS_REVIEW):
        raise DispatchError(
            f"Task {task_id!r} cannot be dispatched from status={current_status!r}"
        )

    # Promote trigger_type so it sorts first in the queue
    task_hub.upsert_item(conn, {"task_id": task_id, "trigger_type": "immediate"})

    claimed = task_hub.claim_next_dispatch_tasks(
        conn,
        limit=1,
        agent_id=agent_id,
        trigger_types=["immediate"],
    )
    _enrich_with_routing(claimed)

    # Find our specific task in the claimed list
    for c in claimed:
        if str(c.get("task_id")) == task_id:
            log.info("dispatch_immediate: claimed task_id=%s assignment=%s routing=%s", task_id, c.get("assignment_id"), c.get("_routing", {}).get("agent_id", "?"))
            return c

    raise DispatchError(f"Task {task_id!r} was not claimed — it may already be in-progress or seized")


# ---------------------------------------------------------------------------
# Approval dispatch — dashboard "Approve"
# ---------------------------------------------------------------------------

def dispatch_on_approval(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    agent_id: str = "dashboard",
) -> dict[str, Any]:
    """Transition a review task to open+human_approved and claim it.

    Returns the claimed assignment dict on success.
    Raises DispatchError if the task doesn't exist or is in a terminal state.
    """
    item = task_hub.get_item(conn, task_id)
    if not item:
        raise DispatchError(f"Task {task_id!r} not found")

    current_status = str(item.get("status") or "").lower()
    if current_status in task_hub.TERMINAL_STATUSES:
        raise DispatchError(
            f"Task {task_id!r} is in terminal status={current_status!r}, cannot approve"
        )

    task_hub.upsert_item(conn, {
        "task_id": task_id,
        "status": task_hub.TASK_STATUS_OPEN,
        "trigger_type": "human_approved",
        "agent_ready": True,  # Human approval makes the task agent-ready
    })

    claimed = task_hub.claim_next_dispatch_tasks(
        conn,
        limit=1,
        agent_id=agent_id,
    )
    _enrich_with_routing(claimed)

    for c in claimed:
        if str(c.get("task_id")) == task_id:
            log.info("dispatch_on_approval: claimed task_id=%s assignment=%s routing=%s", task_id, c.get("assignment_id"), c.get("_routing", {}).get("agent_id", "?"))
            return c

    raise DispatchError(f"Task {task_id!r} could not be claimed after approval")


# ---------------------------------------------------------------------------
# Scheduled dispatch — timer loop
# ---------------------------------------------------------------------------

def dispatch_scheduled_due(
    conn: sqlite3.Connection,
    *,
    agent_id: str = "scheduler",
    as_of_iso: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Find and dispatch all scheduled tasks whose due_at has arrived.

    Returns list of claimed assignment dicts (may be empty).
    """
    due_tasks = task_hub.list_due_scheduled_tasks(conn, as_of_iso=as_of_iso)
    if not due_tasks:
        return []

    all_claimed: list[dict[str, Any]] = []
    for task in due_tasks:
        tid = str(task.get("task_id") or "")
        if not tid:
            continue
        try:
            claimed = task_hub.claim_next_dispatch_tasks(
                conn,
                limit=1,
                agent_id=agent_id,
                trigger_types=["scheduled"],
            )
            for c in claimed:
                if str(c.get("task_id")) == tid:
                    log.info("dispatch_scheduled_due: claimed task_id=%s", tid)
                    all_claimed.append(c)
                    break
        except Exception:
            log.exception("dispatch_scheduled_due: failed to claim task_id=%s", tid)

    return _enrich_with_routing(all_claimed)


# ---------------------------------------------------------------------------
# Sweep dispatch — heartbeat drop-in replacement
# ---------------------------------------------------------------------------

def dispatch_sweep(
    conn: sqlite3.Connection,
    *,
    agent_id: str = "heartbeat",
    limit: int = 1,
    workflow_run_id: Optional[str] = None,
    workflow_attempt_id: Optional[str] = None,
    provider_session_id: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    forbidden_source_kinds: Optional[list[str]] = None,
    additional_running_sessions: Optional[Iterable[str]] = None,
) -> list[dict[str, Any]]:
    """Run a generic sweep dispatch by wrapping claim_next_dispatch_tasks.

    This is the heartbeat's drop-in replacement: it rebuilds the queue and
    claims the top N tasks regardless of trigger_type.

    ``forbidden_source_kinds`` plumbs through to the claim-time SQL filter so
    a caller can exclude task source_kinds that are inappropriate for its
    runtime. Notably, ``daemon_simone_todo`` should pass
    ``forbidden_source_kinds=["vp_mission"]`` so it won't claim VP-mirror
    rows that should be executed by VP workers (Followup #3 backstop).

    ``additional_running_sessions`` (Hermes-adaptation Phase A.2): optional
    iterable of ``provider_session_id`` values for sessions known to be live.
    These are excluded from the stale-assignment release pass that runs at
    the top of every sweep. The calling session (``provider_session_id``) is
    auto-excluded. Pass ``heartbeat_service.busy_sessions``-style sets when
    available so a long-running tick doesn't accidentally release peers.
    """
    # Top-of-sweep stale-release pass (Phase A.2 wiring). Gated by env, runs
    # before the claim so any reopened tasks are eligible in the very next
    # queue rebuild this sweep performs.
    _release_stale_for_sweep(
        conn,
        provider_session_id=provider_session_id,
        additional_running_sessions=additional_running_sessions,
    )

    claimed = task_hub.claim_next_dispatch_tasks(
        conn,
        limit=limit,
        agent_id=agent_id,
        workflow_run_id=workflow_run_id,
        workflow_attempt_id=workflow_attempt_id,
        provider_session_id=provider_session_id,
        workspace_dir=workspace_dir,
        forbidden_source_kinds=forbidden_source_kinds,
    )
    return _enrich_with_routing(claimed)
