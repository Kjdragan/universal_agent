"""Stuck-Run Reaper — progress-based TTL reaping for runaway execution runs.

This module automatically transitions runs stuck in 'running' status to
'timed_out' when they stop making progress (no heartbeat or update) for
longer than their TTL.

Key design principle: **progress-based, not absolute-time**.
  - A run actively heartbeating for 2 hours → fine, keep running.
  - A run that stopped heartbeating 30 minutes ago → stuck, reap it.

Progress is measured via:
  COALESCE(last_heartbeat_at, updated_at)

This is updated by:
  - heartbeat_run_lease() → bumps last_heartbeat_at every lease renewal
  - acquire_run_lease() → sets last_heartbeat_at on initial acquisition
  - update_run_status() → bumps updated_at on any status change
  - update_run_tokens() → bumps updated_at when token counts change

The reaper produces structured ReapedRunInfo objects that include
human-readable notification messages for Simone's investigation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import sqlite3

logger = logging.getLogger(__name__)

# ── Default TTLs per run_kind ────────────────────────────────────────────────

DEFAULT_TODO_TTL_MINUTES = 30
DEFAULT_CRON_TTL_MINUTES = 60
DEFAULT_FALLBACK_TTL_MINUTES = 60


# ── Data Model ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReapedRunInfo:
    """Structured result for each reaped run.

    Includes actionable information for notification/investigation.
    """

    run_id: str
    run_kind: str
    status_before: str
    stale_minutes: float
    ttl_minutes: int
    reason: str  # e.g. "no_progress"
    notification_message: str

    def to_dict(self) -> dict:
        """Run the stuck-run reaper pass once and return a summary."""
        return {
            "run_id": self.run_id,
            "run_kind": self.run_kind,
            "status_before": self.status_before,
            "stale_minutes": round(self.stale_minutes, 1),
            "ttl_minutes": self.ttl_minutes,
            "reason": self.reason,
            "notification_message": self.notification_message,
        }


@dataclass(frozen=True)
class OrphanedAttemptInfo:
    """Structured result for each orphaned attempt finalized by the cleanup pass.

    A run can reach a terminal status (``failed``/``completed``/``cancelled``/
    ``timed_out``/...) through several failure-finalization paths
    (workflow_admission, gateway_server, hooks_service, todo_dispatch, the
    stale-VP reconciler) without finalizing its linked ``run_attempts`` row.
    That leaves the latest attempt parked in ``running``/``queued``/``blocked``
    forever — invisible to :func:`reap_stale_runs` (which scopes to
    ``runs.status = 'running'`` AND ``run_attempts.status = 'running'``) and
    counted permanently by the ``check_stale_runs`` "stuck in running/queued"
    health alert. :func:`finalize_orphaned_run_attempts` reconciles exactly
    those residual attempts.
    """

    run_id: str
    attempt_id: str
    run_status: str
    attempt_status_before: str
    attempt_status_after: str
    terminal_reason: str

    def to_dict(self) -> dict:
        """Return a JSON-serializable summary of the finalized orphan attempt."""
        return {
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "run_status": self.run_status,
            "attempt_status_before": self.attempt_status_before,
            "attempt_status_after": self.attempt_status_after,
            "terminal_reason": self.terminal_reason,
        }


# Attempt statuses that keep a run "live" from the health-alert perspective.
# Mirrors the set counted by ``db_health_monitor.check_stale_runs`` so this
# cleanup targets exactly the rows that would otherwise trip the alert.
_ACTIVE_ATTEMPT_STATUSES: frozenset[str] = frozenset({"running", "queued", "blocked"})

# ── Core Reaper ──────────────────────────────────────────────────────────────

def reap_stale_runs(
    conn: sqlite3.Connection,
    *,
    todo_ttl_minutes: int = DEFAULT_TODO_TTL_MINUTES,
    cron_ttl_minutes: int = DEFAULT_CRON_TTL_MINUTES,
    default_ttl_minutes: int = DEFAULT_FALLBACK_TTL_MINUTES,
) -> list[ReapedRunInfo]:
    """Transition runs stuck in 'running' with no recent progress to 'timed_out'.

    Uses COALESCE(last_heartbeat_at, updated_at) as the last-progress timestamp.
    Different run_kinds get different TTLs:
      - todo_execution: todo_ttl_minutes (default 30)
      - cron_job_dispatch: cron_ttl_minutes (default 60)
      - everything else: default_ttl_minutes (default 60)

    Returns a list of ReapedRunInfo with structured data for notifications.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # Fetch all running runs with their progress timestamp
    rows = conn.execute(
        """
        SELECT
            run_id,
            run_kind,
            status,
            COALESCE(last_heartbeat_at, updated_at) AS last_progress_at,
            created_at
        FROM runs
        WHERE status = 'running'
        """
    ).fetchall()

    reaped: list[ReapedRunInfo] = []
    reap_ids: list[tuple[str, str]] = []  # (run_id, terminal_reason)

    for row in rows:
        run_id = row[0] if not isinstance(row, sqlite3.Row) else row["run_id"]
        run_kind = (row[1] if not isinstance(row, sqlite3.Row) else row["run_kind"]) or ""
        status = row[2] if not isinstance(row, sqlite3.Row) else row["status"]
        last_progress_str = row[3] if not isinstance(row, sqlite3.Row) else row["last_progress_at"]

        # Determine TTL for this run_kind
        kind_lower = run_kind.lower().strip()
        if kind_lower == "todo_execution":
            ttl = todo_ttl_minutes
        elif kind_lower == "cron_job_dispatch":
            ttl = cron_ttl_minutes
        else:
            ttl = default_ttl_minutes

        # Parse last progress timestamp
        if not last_progress_str:
            # No progress timestamp at all — use created_at as fallback
            last_progress_str = row[4] if not isinstance(row, sqlite3.Row) else row["created_at"]

        try:
            # Handle both formats: with and without timezone
            progress_str = str(last_progress_str).strip()
            if progress_str.endswith("Z"):
                progress_str = progress_str[:-1] + "+00:00"
            if "+" not in progress_str and progress_str.count("-") <= 2:
                # Naive datetime — assume UTC
                last_progress = datetime.fromisoformat(progress_str).replace(tzinfo=timezone.utc)
            else:
                last_progress = datetime.fromisoformat(progress_str)
                if last_progress.tzinfo is None:
                    last_progress = last_progress.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            logger.warning("Reaper: cannot parse timestamp for %s: %r", run_id, last_progress_str)
            continue

        stale_seconds = (now - last_progress).total_seconds()
        stale_minutes = stale_seconds / 60.0

        if stale_minutes >= ttl:
            terminal_reason = (
                f"reaper:no_progress:{int(stale_minutes)}m_stale"
                f"(ttl={ttl}m,kind={kind_lower or 'unknown'})"
            )
            reap_ids.append((run_id, terminal_reason))

            notification_msg = (
                f"⏰ Run '{run_id}' (kind={run_kind or 'unknown'}) reaped: "
                f"no progress for {int(stale_minutes)} minutes "
                f"(TTL={ttl}m). Last activity at {last_progress_str}. "
                f"Investigate if this indicates a systemic issue."
            )

            info = ReapedRunInfo(
                run_id=run_id,
                run_kind=run_kind or "unknown",
                status_before=status,
                stale_minutes=stale_minutes,
                ttl_minutes=ttl,
                reason="no_progress",
                notification_message=notification_msg,
            )
            reaped.append(info)

    # Batch-update all reaped runs
    if reap_ids:
        for run_id, terminal_reason in reap_ids:
            conn.execute(
                """
                UPDATE runs
                SET status = 'timed_out',
                    terminal_reason = ?,
                    updated_at = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (terminal_reason, now_iso, run_id),
            )

            # Also update associated run_attempts
            conn.execute(
                """
                UPDATE run_attempts
                SET status = 'timed_out',
                    ended_at = ?,
                    updated_at = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (now_iso, now_iso, run_id),
            )
        conn.commit()

        logger.warning(
            "🪦 Reaper: reaped %d stuck runs: %s",
            len(reaped),
            ", ".join(r.run_id for r in reaped),
        )

    return reaped


# Statuses mirrored from a terminal run onto its residual orphan attempt. The
# run/attempt status vocabularies overlap; anything unexpected falls back to
# ``failed`` (see :func:`finalize_orphaned_run_attempts`).
_MIRRORABLE_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"failed", "cancelled", "completed", "succeeded", "timed_out", "needs_review"}
)


def finalize_orphaned_run_attempts(
    conn: sqlite3.Connection,
    *,
    max_rows: int = 500,
) -> list[OrphanedAttemptInfo]:
    """Finalize ``run_attempts`` rows left non-terminal when their run already ended.

    Reconciles the orphan state behind the ``check_stale_runs`` "stuck in
    running/queued >2.0h" alert. A failure-finalization path can mark
    ``runs.status`` terminal (e.g. ``failed`` with ``terminal_reason =
    'hook_dispatch_failed'``) without ever moving the linked ``run_attempts``
    row out of ``running``/``queued``/``blocked``. That residual attempt is then:

      * invisible to :func:`reap_stale_runs` — it scopes to
        ``runs.status = 'running'`` AND ``run_attempts.status = 'running'``, so a
        *failed* run with a *queued* attempt is never reached; and
      * counted forever by ``check_stale_runs``, which joins
        ``run_attempts.status IN ('running', 'queued', 'blocked')``.

    This pass makes the invariant "a terminal run has only terminal attempts"
    hold regardless of which failure path produced the orphan. It is strictly
    additive bookkeeping — the run already recorded its terminal outcome, so we
    mirror that outcome onto the residual attempt and clear its lease. It
    deliberately does **not** surface a failure card: the run's
    ``terminal_reason`` already carries the failure, and re-surfacing would
    duplicate the ``vp_failure`` / rescue path.

    Args:
        conn: ``runtime_state.db`` connection (``runs`` + ``run_attempts``).
        max_rows: Cap on attempts finalized per pass (bounded work per heartbeat).

    Returns:
        One :class:`OrphanedAttemptInfo` per attempt finalized this pass.

    Idempotent: the ``WHERE ... AND status IN (active set)`` guard makes a repeat
    call a no-op for already-finalized attempts. Legitimate in-progress runs
    (``runs.status`` still in the active set) are never touched.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    rows = conn.execute(
        """
        SELECT r.run_id,
               r.run_kind,
               r.status        AS run_status,
               r.terminal_reason,
               a.attempt_id,
               a.status        AS attempt_status,
               a.started_at
        FROM run_attempts a
        JOIN runs r ON r.run_id = a.run_id
        WHERE a.status IN ('running', 'queued', 'blocked')
          AND r.status NOT IN ('running', 'queued', 'blocked')
        ORDER BY a.started_at ASC
        LIMIT ?
        """,
        (max(1, int(max_rows)),),
    ).fetchall()

    finalized: list[OrphanedAttemptInfo] = []
    for row in rows:
        is_row = isinstance(row, sqlite3.Row)
        run_id = row["run_id"] if is_row else row[0]
        run_status = (row["run_status"] if is_row else row[2]) or ""
        terminal_reason = (row["terminal_reason"] if is_row else row[3]) or ""
        attempt_id = row["attempt_id"] if is_row else row[4]
        attempt_status_before = (row["attempt_status"] if is_row else row[5]) or ""

        # Mirror the run's outcome onto the attempt; fall back to 'failed'.
        attempt_status_after = (
            run_status if run_status in _MIRRORABLE_TERMINAL_STATUSES else "failed"
        )
        failure_reason = (
            f"orphaned_attempt_cleanup:parent_run_{run_status or 'terminal'}"
            f":{terminal_reason or 'terminal'}"
        )
        conn.execute(
            """
            UPDATE run_attempts
            SET status = ?,
                ended_at = COALESCE(ended_at, ?),
                lease_owner = NULL,
                lease_expires_at = NULL,
                failure_class = COALESCE(failure_class, 'orphaned_attempt_cleanup'),
                failure_reason = COALESCE(failure_reason, ?),
                updated_at = ?
            WHERE attempt_id = ? AND status IN ('running', 'queued', 'blocked')
            """,
            (attempt_status_after, now_iso, failure_reason, now_iso, attempt_id),
        )
        finalized.append(
            OrphanedAttemptInfo(
                run_id=run_id,
                attempt_id=attempt_id,
                run_status=run_status,
                attempt_status_before=attempt_status_before,
                attempt_status_after=attempt_status_after,
                terminal_reason=terminal_reason,
            )
        )

    if finalized:
        conn.commit()
        logger.warning(
            "🧹 Orphan-attempt cleanup: finalized %d residual run_attempt(s) "
            "left non-terminal by a run failure path: %s",
            len(finalized),
            ", ".join(f.attempt_id for f in finalized),
        )
    return finalized
