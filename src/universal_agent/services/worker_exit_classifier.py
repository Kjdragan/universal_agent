"""Hermes Phase F.1 — Worker exit classification.

Classifies the termination of an owned-subprocess worker (cron `!script`,
VP CLI client, demo workspace) into one of five buckets so Task Hub can
distinguish "clean success" from "clean exit but didn't close its task"
(protocol violation — F.3) and from real errors.

The classifier is pure — it doesn't touch DB or sockets. Call from each
spawn site immediately after the subprocess completes, then thread the
returned classification into ``task_hub._close_run`` via the ``outcome``
parameter.

The classification taxonomy mirrors Hermes' kanban_db.py:2879-2911
``_classify_worker_exit`` but is scoped to UA's three subprocess sites.

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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

WorkerOutcome = Literal[
    "clean_exit_zero",
    "clean_exit_zero_no_disposition",
    "nonzero_exit",
    "signaled",
    "timeout_killed",
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


def classify_worker_exit(
    *,
    return_code: int | None,
    was_signaled: bool = False,
    was_timeout_killed: bool = False,
    task_closed_normally: bool = True,
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

    Returns:
        A ``WorkerExit`` record.
    """
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


__all__ = [
    "WorkerOutcome",
    "WorkerExit",
    "classify_worker_exit",
    "PROTOCOL_VIOLATION_REASONS",
]
