"""Hermes Phase F follow-up — auto-ensure Task Hub linkage for cron jobs.

Background
----------
The Hermes Phase F site-wiring in :mod:`cron_service` records ``worker_pid``
and detects protocol violations (rc=0 + task still ``in_progress``) only when
the cron job's ``metadata.task_id`` is populated.  As shipped in PR #238, the
only producer that populated that key was the email task scheduler — every
other ``!script`` cron source skipped the F.1/F.3 hooks silently.

This module closes the gap.  It provides a single helper,
:func:`ensure_cron_task_link`, which the cron `!script` spawn site calls
right before forking the subprocess.  The helper:

1. Returns immediately if the job has opted out
   (``metadata.skip_task_hub_link == True``) — used by housekeeping crons.
2. If the job already carries an explicit ``metadata.task_id`` (e.g. the
   email scheduler path), returns that ``task_id`` verbatim — the existing
   F.1/F.3 wiring needs no further help.
3. Otherwise, derives a stable ``task_id = "cron:<system_job>"`` (falling
   back to ``cron:<job_id>`` if ``system_job`` is not set), upserts a
   ``task_hub_items`` row (idempotent — repeated cron runs reuse the same
   row), and creates a fresh ``task_hub_assignments`` row plus a
   ``task_hub_runs`` row so the F.1 PID-stamp / F.3 close-run / protocol
   violation routing all hook up the same way they do for email-sourced
   crons.

Design notes
------------
* **Stable task per cron source (Pattern A).**  Each cron name maps to a
  single perpetual ``task_hub_items`` row — re-runs append a new
  assignment / run row but reuse the task.  This keeps the task list
  manageable while ``task_hub_runs`` still preserves a per-run audit
  trail (which is exactly what Hermes Phase D was built for).

* **agent_ready = False.**  Cron drives itself; we must not let the
  dispatch sweep claim a ``cron:<name>`` task from underneath the
  scheduler.

* **status lifecycle for auto-linked cron tasks.**

  1. :func:`ensure_cron_task_link` upserts the task as ``in_progress``
     at spawn time so each run has a clear "active" signal.
  2. Just before the F.3 classifier runs, the cron site wiring flips
     the task to ``completed`` on a clean rc=0 exit (auto-linked cron
     scripts don't manage their own task lifecycle, so a clean exit
     IS the "done" signal).  This makes ``task_was_closed_normally``
     return True and the classifier picks ``clean_exit_zero`` rather
     than ``protocol_violation``.
  3. :func:`close_cron_task_link` then flips ``completed`` back to
     ``open`` so the next cron tick can re-claim the perpetual task
     without creating a new row.

* **Best-effort.**  Every code path swallows DB errors and logs at
  ``debug`` — the cron run must never break because Task Hub linkage
  failed.  ``ensure_cron_task_link`` returning ``None`` is a valid
  ``no linkage`` signal that the spawn site interprets as "skip F.1/F.3
  silently" (the pre-PR behavior).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Optional
import uuid

from universal_agent import task_hub

logger = logging.getLogger(__name__)


_CRON_AGENT_ID = "cron_scheduler"

# Source kind used for auto-linked cron task rows.  Stable across the
# codebase — close_cron_task_link uses this as a guard so it only
# resets cron-owned rows, never accidental email-scheduler rows that
# happen to be in completed status.
CRON_TASK_SOURCE_KIND = "cron_run"


def derive_cron_task_id(*, system_job: str | None, job_id: str | None) -> Optional[str]:
    """Derive the canonical ``cron:<name>`` task_id for a cron job.

    Prefers ``system_job`` (the canonical key registered via
    ``_register_system_cron_job``); falls back to ``job_id`` for ad-hoc
    crons that don't go through the system-job helper.  Returns ``None``
    if neither value is usable — the caller should treat that as
    "no linkage available, skip F.1/F.3".
    """
    sj = str(system_job or "").strip()
    if sj:
        return f"cron:{sj}"
    jid = str(job_id or "").strip()
    if jid:
        return f"cron:{jid}"
    return None


def ensure_cron_task_link(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    job_metadata: dict[str, Any] | None,
    description: str = "",
) -> Optional[dict[str, str]]:
    """Auto-ensure a stable Task Hub row + fresh assignment for a cron run.

    Returns a dict ``{"task_id": ..., "assignment_id": ...}`` on success
    so the spawn site can stamp ``worker_pid`` onto the assignment.

    Returns ``None`` if:

    * the job opted out (``metadata.skip_task_hub_link == True``);
    * the linkage helper failed (best-effort — DB errors return ``None``
      and log at ``debug`` so the cron run proceeds unblocked);
    * the job had no derivable ``task_id`` (both ``system_job`` and
      ``job_id`` were empty / missing — should never happen in practice).

    If the job already carries ``metadata.task_id`` (e.g. email scheduler
    crons), the function returns a dict with that explicit task_id and
    ``assignment_id=""`` — the caller's existing ``find_active_assignment_for_task``
    path handles assignment lookup for those crons.
    """
    metadata = dict(job_metadata or {})

    # Opt-out for housekeeping crons.
    if metadata.get("skip_task_hub_link") is True:
        return None

    # Caller-supplied task_id (e.g. email scheduler) — pre-existing path.
    explicit_task_id = str(metadata.get("task_id") or "").strip()
    if explicit_task_id:
        return {"task_id": explicit_task_id, "assignment_id": ""}

    system_job = str(metadata.get("system_job") or "").strip()
    task_id = derive_cron_task_id(system_job=system_job, job_id=job_id)
    if not task_id:
        return None

    try:
        # 1. Idempotent upsert of the perpetual cron task row.
        title = f"Cron: {system_job or job_id}"
        task_hub.upsert_item(
            conn,
            {
                "task_id": task_id,
                "source_kind": CRON_TASK_SOURCE_KIND,
                "source_ref": system_job or job_id,
                "title": title,
                "description": description or title,
                # `agent_ready = False` is the load-bearing flag: it tells
                # the dispatch sweep "do not claim this task — the cron
                # scheduler owns it".  Without this, a stable cron task
                # could be grabbed by a heartbeat and processed twice.
                "agent_ready": False,
                "status": task_hub.TASK_STATUS_IN_PROGRESS,
                "metadata": {
                    "cron_owned": True,
                    "system_job": system_job,
                    "job_id": job_id,
                },
            },
        )

        # 2. Fresh assignment row for THIS run.  Each run gets its own
        # assignment + run so F.1 PID stamping / F.3 close-run can
        # correlate via assignment_id without colliding with prior runs.
        assignment_id = f"asg_cron_{uuid.uuid4().hex[:16]}"
        now_iso = task_hub._now_iso()  # type: ignore[attr-defined]
        conn.execute(
            """
            INSERT INTO task_hub_assignments (
                assignment_id, task_id, agent_id, state, started_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (assignment_id, task_id, _CRON_AGENT_ID, "running", now_iso),
        )

        # 3. Phase D run row so the operator dashboard surfaces the
        # per-attempt outcome alongside any other Task Hub run.
        try:
            task_hub._open_run(  # type: ignore[attr-defined]
                conn,
                task_id=task_id,
                assignment_id=assignment_id,
                agent_id=_CRON_AGENT_ID,
                metadata={
                    "claim_source": "cron_auto_link",
                    "system_job": system_job,
                    "job_id": job_id,
                },
            )
        except Exception as run_exc:  # pragma: no cover — defensive
            logger.debug(
                "ensure_cron_task_link: _open_run failed for job=%s: %s",
                job_id, run_exc,
            )

        conn.commit()
        return {"task_id": task_id, "assignment_id": assignment_id}
    except Exception as exc:
        logger.debug(
            "ensure_cron_task_link failed for job=%s (continuing without linkage): %s",
            job_id, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def close_cron_task_link(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    success: bool,
) -> None:
    """Reset a perpetual cron task back to ``open`` after a clean run.

    Lifecycle for auto-linked cron tasks (full picture in module docstring):

    1. ``ensure_cron_task_link`` upserts the task as ``in_progress``.
    2. Cron site wiring flips it to ``completed`` before F.3 classifier.
    3. THIS function flips ``completed`` back to ``open``.

    On failure (``success=False``) or if F.3 moved the task to
    ``needs_review``, the task is left at whatever state the wiring
    set — those are intentional surfacing signals for the operator.

    Best-effort; never raises.
    """
    tid = str(task_id or "").strip()
    if not tid:
        return
    try:
        item = task_hub.get_item(conn, tid)
        if not item:
            return
        # Guard: only reset rows we own (source_kind == cron_run) AND
        # only on success AND only when the status is one of the
        # expected "happy path" states.  This avoids stomping on a
        # ``needs_review`` left by F.3 (a real protocol violation we
        # want the operator to see) or a ``blocked`` set manually.
        current_status = str(item.get("status") or "")
        if (
            str(item.get("source_kind") or "") == CRON_TASK_SOURCE_KIND
            and success
            and current_status in {
                task_hub.TASK_STATUS_IN_PROGRESS,
                task_hub.TASK_STATUS_COMPLETED,
            }
        ):
            conn.execute(
                "UPDATE task_hub_items SET status = ?, seizure_state = ?, updated_at = ? "
                "WHERE task_id = ?",
                (task_hub.TASK_STATUS_OPEN, "unseized", task_hub._now_iso(), tid),  # type: ignore[attr-defined]
            )
            conn.commit()
    except Exception as exc:
        logger.debug(
            "close_cron_task_link failed for task=%s (continuing): %s",
            tid, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass


__all__ = [
    "CRON_TASK_SOURCE_KIND",
    "derive_cron_task_id",
    "ensure_cron_task_link",
    "close_cron_task_link",
]
