"""Hermes Phase F.1 — Worker exit classification.

Classifies the termination of an owned-subprocess worker (cron `!script`,
VP CLI client, demo workspace) or an in-process LLM coroutine
(cron LLM-prompt path) into one of six buckets so Task Hub can
distinguish "clean success" from "clean exit but didn't close its task"
(protocol violation — F.3) and from real errors.

The classifier is pure — it doesn't touch DB or sockets. Call from each
spawn site immediately after the subprocess completes, then thread the
returned classification into ``task_hub._close_run`` via the ``outcome``
parameter.

The classification taxonomy mirrors Hermes' kanban_db.py:2879-2911
``_classify_worker_exit`` but is scoped to UA's three subprocess sites
(and the in-process LLM cron path, which uses cancellation detection
rather than a return code).

Outcomes:
    * ``clean_exit_zero`` — rc=0 AND task was closed via finalize_assignments
      or perform_task_action(action="complete"). Normal success.
    * ``clean_exit_zero_no_disposition`` — rc=0 but task is still in_progress.
      Protocol violation — F.3 routes this to needs_review with a
      site-specific reason string. The subprocess "succeeded" but didn't
      tell Task Hub what it did, so we can't trust it.
    * ``nonzero_exit`` — rc != 0. Real error. Increment failure counter
      via existing finalize path; retry per existing rules.
    * ``signaled`` — process killed by signal (OOM, SIGKILL from outside).
      Treated as a failure but signals a system-level event worth flagging.
    * ``timeout_killed`` — UA's own timeout machinery killed it. Distinct
      from ``signaled`` because the kill was intentional and the worker
      may have made partial progress worth surfacing in the run's metadata.
    * ``cancelled_mid_run`` — the coroutine was cancelled externally
      (gateway session reaper, operator action). Distinct from
      ``timeout_killed`` (UA's own timeout) and ``signaled`` (OS-level
      kill to a subprocess). Added 2026-05-13 after the gateway-freeze
      incident where reaped LLM-cron coroutines were mis-painted as
      ``clean_exit_zero``. See plans/2026-05-13_proactivity_gap_findings.md.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import sqlite3
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

WorkerOutcome = Literal[
    "clean_exit_zero",
    "clean_exit_zero_no_disposition",
    "nonzero_exit",
    "signaled",
    "timeout_killed",
    "cancelled_mid_run",
]


@dataclass(frozen=True)
class WorkerExit:
    """Result of classifying a subprocess termination.

    Attributes:
        outcome: One of the five ``WorkerOutcome`` values above.
        is_protocol_violation: True iff ``outcome ==
            "clean_exit_zero_no_disposition"``. Convenience flag for the
            caller; F.3 wiring uses this to decide whether to route the
            task into needs_review.
        is_failure: True iff the outcome should bump the task's retry
            counter. Excludes ``clean_exit_zero`` and protocol violations
            (the latter is handled by F.3's needs_review path, not the
            normal retry budget).

    """

    outcome: WorkerOutcome
    is_protocol_violation: bool
    is_failure: bool

    def to_dict(self) -> dict[str, Any]:
        """Classify the worker exit and return the resulting decision."""
        return {
            "outcome": self.outcome,
            "is_protocol_violation": self.is_protocol_violation,
            "is_failure": self.is_failure,
        }


def classify_worker_exit(
    *,
    return_code: int | None,
    was_signaled: bool = False,
    was_timeout_killed: bool = False,
    task_closed_normally: bool = True,
    was_cancelled: bool = False,
) -> WorkerExit:
    """Classify a subprocess termination into one of the F.1 outcome buckets.

    Args:
        return_code: The subprocess's returncode. ``None`` if not
            available (e.g. process killed before we could read it).
        was_signaled: ``True`` if the process was terminated by a signal
            (negative returncode in POSIX semantics, or our own
            ``proc.kill()`` if it raced past our timeout window).
            Distinct from ``was_timeout_killed``.
        was_timeout_killed: ``True`` iff UA's own timeout machinery
            (e.g. ``asyncio.wait_for`` raising ``TimeoutError`` and us
            calling ``proc.kill()``) terminated the process.
        task_closed_normally: ``True`` iff the linked task_hub item was
            successfully closed via ``finalize_assignments`` or
            ``perform_task_action(action="complete")`` BEFORE this
            classification ran. Distinguishes clean success from
            protocol violation.
        was_cancelled: ``True`` iff the coroutine was cancelled mid-run
            (e.g. the gateway session reaper called ``task.cancel()``
            after the 600s TTL elapsed, or the cron was operator-
            cancelled via ``task_hub_task_action``). ``asyncio.CancelledError``
            inherits from ``BaseException`` (not ``Exception``), so it
            bypasses generic ``except Exception`` handlers — callers
            must explicitly detect it and pass ``was_cancelled=True``.
            Distinct from ``was_timeout_killed`` (UA's own timeout) and
            ``was_signaled`` (OS-level signal to a subprocess).

    Returns:
        A ``WorkerExit`` record.

    """
    if was_cancelled:
        return WorkerExit(
            outcome="cancelled_mid_run",
            is_protocol_violation=False,
            is_failure=True,
        )
    if was_timeout_killed:
        return WorkerExit(
            outcome="timeout_killed",
            is_protocol_violation=False,
            is_failure=True,
        )
    if was_signaled:
        return WorkerExit(
            outcome="signaled",
            is_protocol_violation=False,
            is_failure=True,
        )
    if return_code is None or return_code != 0:
        return WorkerExit(
            outcome="nonzero_exit",
            is_protocol_violation=False,
            is_failure=True,
        )
    # rc == 0 — distinguish clean success from clean-exit-no-disposition.
    if task_closed_normally:
        return WorkerExit(
            outcome="clean_exit_zero",
            is_protocol_violation=False,
            is_failure=False,
        )
    return WorkerExit(
        outcome="clean_exit_zero_no_disposition",
        is_protocol_violation=True,
        # Protocol violations are routed to needs_review via F.3, not
        # incremented as failures in the retry budget.
        is_failure=False,
    )


# Reason strings used by F.3 to populate `last_disposition_reason` on
# tasks that are routed to needs_review because their owned subprocess
# exited cleanly without closing the task.
PROTOCOL_VIOLATION_REASONS: dict[str, str] = {
    "cron": "protocol_violation_cron_clean_exit_no_disposition",
    "vp_cli": "protocol_violation_vp_cli_clean_exit_no_disposition",
    "demo": "protocol_violation_demo_clean_exit_no_disposition",
}


# F.3 sentinel — sites pass the string key into ``park_task_for_protocol_violation``
# so the helper looks up the canonical reason. Keeps the reason vocabulary
# centralized in this module.
_VALID_SITES = frozenset(PROTOCOL_VIOLATION_REASONS.keys())


def park_task_for_protocol_violation(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    site: str,
    summary: str = "",
    agent_id: str = "worker_exit_classifier",
) -> bool:
    """Hermes Phase F.3 — route a protocol-violating task into ``needs_review``.

    Called from each owned-subprocess spawn site (cron / VP CLI) when
    ``classify_worker_exit`` returns ``is_protocol_violation=True`` — the
    subprocess exited rc=0 but never closed its task via
    ``finalize_assignments`` or ``perform_task_action(action="complete")``.
    Phase B.1's ``rehydrate`` / ``re_evaluate`` verbs then surface the
    parked task for Simone to act on.

    Args:
        conn: A live Task Hub connection. The caller owns the
            transaction — this function commits before returning.
        task_id: The Task Hub item to route. If empty or unknown the
            call is a no-op and returns ``False``.
        site: One of ``"cron"`` | ``"vp_cli"`` | ``"demo"``. Determines
            which canonical reason string from ``PROTOCOL_VIOLATION_REASONS``
            is recorded on the task.
        summary: Optional short text appended to the reason. Surfaces in
            the task's ``last_disposition_reason`` metadata field so the
            operator (or Simone, post-Phase D) can see *what* the
            worker was attempting when it bailed silently.
        agent_id: The agent attribution string written to the
            evaluation/action audit trail. Defaults to
            ``"worker_exit_classifier"`` to make protocol-violation parks
            grep-able in the audit log.

    Returns:
        ``True`` if the task was parked, ``False`` if the call was a
        no-op (missing task_id, unknown task, unknown site, or any
        ``perform_task_action`` failure). Best-effort by design — F.3
        is observability + recovery routing; it must never raise into the
        spawn site's happy path.

    """
    tid = str(task_id or "").strip()
    if not tid:
        return False
    site_norm = str(site or "").strip().lower()
    if site_norm not in _VALID_SITES:
        logger.warning(
            "park_task_for_protocol_violation called with unknown site=%r; "
            "valid: %s",
            site, sorted(_VALID_SITES),
        )
        return False

    reason = PROTOCOL_VIOLATION_REASONS[site_norm]
    if summary:
        # Keep the canonical reason key as the leading token so downstream
        # filters / log scrapers stay simple.
        combined_reason = f"{reason}: {summary.strip()[:300]}"
    else:
        combined_reason = reason

    try:
        # Lazy import to avoid a hard module-level dependency cycle with
        # task_hub (task_hub may import from services in the future).
        from universal_agent import (
            task_hub,  # noqa: WPS433 (local import is intentional)
        )

        task_hub.perform_task_action(
            conn,
            task_id=tid,
            action="review",
            reason=combined_reason,
            agent_id=agent_id,
        )
        try:
            conn.commit()
        except Exception:
            # Some callers wrap us in a larger transaction; don't blow up.
            pass
        logger.info(
            "Phase F.3 parked task %s in needs_review (site=%s, reason=%s)",
            tid, site_norm, reason,
        )
        return True
    except Exception as exc:
        logger.warning(
            "Phase F.3 park failed for task %s (site=%s): %s",
            tid, site_norm, exc,
        )
        return False


def find_active_assignment_for_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
) -> Optional[str]:
    """Look up the most recent active assignment_id for a task.

    Active = ``state IN ('seized', 'running')``. Used by spawn sites to
    correlate their just-spawned subprocess back to a Task Hub assignment
    row (so ``record_worker_pid`` knows which row to stamp).

    Returns ``None`` if the task has no active assignment (which is the
    common case for cron-only jobs that don't go through Task Hub).
    Never raises — wraps DB errors in a warning log and returns ``None``.
    """
    tid = str(task_id or "").strip()
    if not tid:
        return None
    try:
        row = conn.execute(
            """
            SELECT assignment_id
            FROM task_hub_assignments
            WHERE task_id = ? AND state IN ('seized', 'running')
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (tid,),
        ).fetchone()
        if not row:
            return None
        # Support both Row (mapping access) and tuple cursor row_factory.
        try:
            return str(row["assignment_id"])
        except (TypeError, IndexError, KeyError):
            return str(row[0])
    except Exception as exc:
        logger.debug(
            "find_active_assignment_for_task(%s) failed: %s", tid, exc,
        )
        return None


def task_was_closed_normally(
    conn: sqlite3.Connection,
    *,
    task_id: str,
) -> bool:
    """Return True iff the task is in a terminal-success-ish state.

    Used by spawn sites to feed ``classify_worker_exit``'s
    ``task_closed_normally`` flag. ``completed`` (the normal success
    path) is the canonical signal. ``cancelled`` is also acceptable
    because the operator/Simone explicitly closed the task — it's not a
    protocol violation if the worker happened to exit rc=0 after the
    task was cancelled out from under it.

    States that explicitly mean "still in flight" or "needs human
    follow-up" return ``False`` so F.3 routes them.

    Returns ``False`` on unknown task / DB error (conservative —
    prevents spurious protocol-violation parks against tasks we can't
    even read).
    """
    tid = str(task_id or "").strip()
    if not tid:
        return False
    try:
        row = conn.execute(
            "SELECT status FROM task_hub_items WHERE task_id = ? LIMIT 1",
            (tid,),
        ).fetchone()
        if not row:
            return False
        try:
            status = str(row["status"] or "").strip().lower()
        except (TypeError, IndexError, KeyError):
            status = str(row[0] or "").strip().lower()
        return status in {"completed", "cancelled"}
    except Exception as exc:
        logger.debug("task_was_closed_normally(%s) failed: %s", tid, exc)
        return False


__all__ = [
    "WorkerOutcome",
    "WorkerExit",
    "classify_worker_exit",
    "PROTOCOL_VIOLATION_REASONS",
    "park_task_for_protocol_violation",
    "find_active_assignment_for_task",
    "task_was_closed_normally",
]
