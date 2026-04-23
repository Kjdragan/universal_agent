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

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

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
        return {
            "run_id": self.run_id,
            "run_kind": self.run_kind,
            "status_before": self.status_before,
            "stale_minutes": round(self.stale_minutes, 1),
            "ttl_minutes": self.ttl_minutes,
            "reason": self.reason,
            "notification_message": self.notification_message,
        }


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
