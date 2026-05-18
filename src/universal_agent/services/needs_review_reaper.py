"""needs_review_reaper.py — SLA reaper for stuck-in-review tasks.

``needs_review`` is the Task Hub's uncertainty-disposition escape hatch:
the finalize sweep in ``task_hub.py`` parks a task there when a run
finishes but didn't reach an explicit completed/failed disposition
(see ``task_hub.py:3460+``). The status is overloaded — it covers
both *"finished ambiguously, retry would be safe"* and *"side effects
occurred, human must look"*.

Until this module landed, there was no automatic recovery from
``needs_review``. Tasks could rot for days. The 39-hour delay on the
"Hermes AI Desktop App" wiki on 2026-05-17/18 was traced to exactly
this hole.

This reaper sweeps tasks whose ``metadata.dispatch.last_disposition_reason``
indicates *disposition uncertainty* and re-opens them after an SLA.
Operator-gated reasons (retry exhausted, side effects already happened)
are deliberately left alone.

Configuration:
  - UA_NEEDS_REVIEW_SLA_HOURS (default: 4) — age before recovery
  - UA_NEEDS_REVIEW_REAPER_LIMIT (default: 20) — per-sweep cap
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
import sqlite3
from typing import Any, Optional

from universal_agent import task_hub

logger = logging.getLogger(__name__)

DEFAULT_SLA_HOURS = 4
DEFAULT_PER_SWEEP_LIMIT = 20

# Disposition reasons set by task_hub finalize when a run completes
# without an explicit close. These are safe to retry.
RECOVERABLE_REASONS = frozenset({
    "heartbeat_completed_without_disposition",
    "todo_completed_without_disposition",
})

# Disposition reasons that genuinely require operator attention — never
# auto-recover. Side effects already occurred, or the task burned its
# retry budget.
OPERATOR_GATED_REASONS = frozenset({
    "heartbeat_retry_exhausted",
    "heartbeat_retryable_with_side_effects",
    "todo_retryable_with_side_effects",
})


def _parse_int_env(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def _disposition_reason(metadata_json: str) -> str:
    """Extract ``dispatch.last_disposition_reason`` from a metadata blob."""
    try:
        metadata = json.loads(metadata_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(metadata, dict):
        return ""
    dispatch = metadata.get("dispatch") or {}
    if not isinstance(dispatch, dict):
        return ""
    return str(dispatch.get("last_disposition_reason") or "")


def reap_stale_needs_review(
    conn: sqlite3.Connection,
    *,
    sla_hours: Optional[int] = None,
    limit: Optional[int] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Re-open stale ``needs_review`` tasks with recoverable dispositions.

    Args:
        conn: Database connection (will commit when any recovery happens).
        sla_hours: Override the SLA. Defaults to ``UA_NEEDS_REVIEW_SLA_HOURS``
            or 4 hours.
        limit: Maximum tasks to recover in one sweep. Defaults to
            ``UA_NEEDS_REVIEW_REAPER_LIMIT`` or 20.
        now: Test-injection point for "current time".

    Returns:
        Summary dict with counts and the recovered task IDs.
    """
    task_hub.ensure_schema(conn)
    effective_sla = (
        sla_hours
        if sla_hours is not None
        else _parse_int_env("UA_NEEDS_REVIEW_SLA_HOURS", DEFAULT_SLA_HOURS)
    )
    effective_limit = (
        limit
        if limit is not None
        else _parse_int_env("UA_NEEDS_REVIEW_REAPER_LIMIT", DEFAULT_PER_SWEEP_LIMIT)
    )
    now_dt = now or datetime.now(timezone.utc)
    cutoff = (now_dt - timedelta(hours=effective_sla)).isoformat()
    now_iso = now_dt.isoformat()

    # Over-fetch so the in-Python filter for disposition reason still
    # gives us a reasonable working set even when many rows are
    # operator-gated. Cap at limit * 4 to keep memory bounded.
    rows = conn.execute(
        """
        SELECT task_id, metadata_json, updated_at
        FROM task_hub_items
        WHERE status = ?
          AND updated_at < ?
        ORDER BY updated_at ASC
        LIMIT ?
        """,
        (task_hub.TASK_STATUS_REVIEW, cutoff, max(effective_limit * 4, effective_limit)),
    ).fetchall()

    recovered_ids: list[str] = []
    skipped_gated = 0
    skipped_unknown = 0

    for row in rows:
        if len(recovered_ids) >= effective_limit:
            break
        reason = _disposition_reason(row["metadata_json"])
        if reason in OPERATOR_GATED_REASONS:
            skipped_gated += 1
            continue
        if reason not in RECOVERABLE_REASONS:
            skipped_unknown += 1
            continue

        try:
            metadata = json.loads(row["metadata_json"] or "{}")
            if not isinstance(metadata, dict):
                metadata = {}
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        dispatch_meta = metadata.get("dispatch") or {}
        if not isinstance(dispatch_meta, dict):
            dispatch_meta = {}

        dispatch_meta["last_disposition"] = "reopened"
        dispatch_meta["last_disposition_reason"] = "needs_review_sla_recovered"
        dispatch_meta["needs_review_recovered_at"] = now_iso
        dispatch_meta["needs_review_prior_reason"] = reason
        prior_count = 0
        try:
            prior_count = int(dispatch_meta.get("needs_review_recovery_count") or 0)
        except (TypeError, ValueError):
            prior_count = 0
        dispatch_meta["needs_review_recovery_count"] = prior_count + 1
        metadata["dispatch"] = dispatch_meta

        # Guard against double-flip race by re-checking status in the WHERE.
        conn.execute(
            """
            UPDATE task_hub_items
            SET status = ?, seizure_state = 'unseized', metadata_json = ?, updated_at = ?
            WHERE task_id = ? AND status = ?
            """,
            (
                task_hub.TASK_STATUS_OPEN,
                json.dumps(metadata),
                now_iso,
                row["task_id"],
                task_hub.TASK_STATUS_REVIEW,
            ),
        )
        recovered_ids.append(row["task_id"])
        logger.info(
            "needs_review SLA reaper: reopened task %s (prior_reason=%s, recovery_count=%d)",
            row["task_id"],
            reason,
            prior_count + 1,
        )

    if recovered_ids:
        conn.commit()

    return {
        "recovered": len(recovered_ids),
        "recovered_ids": recovered_ids,
        "skipped_gated": skipped_gated,
        "skipped_unknown": skipped_unknown,
        "sla_hours": effective_sla,
    }
