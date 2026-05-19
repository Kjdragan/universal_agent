"""Proactive activity health aggregator — Layer 1 + Layer 2.

Produces the payload served by GET /api/v1/ops/proactive_health.  Layer 1
is process-liveness (cron job last-run age, stale in-progress tasks, parked
tasks).  Layer 2 is the pipeline-invariants registry from
`pipeline_invariants.run_invariants`.

Pulling the aggregation out of the FastAPI handler keeps it import-safe and
lets the unit tests build payloads against seeded SQLite without spinning
up the full gateway.  The handler in gateway_server.py is a thin wrapper.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

# Importing this package registers all built-in invariants.
from universal_agent.services import (
    invariants as _invariants,  # noqa: F401
    pipeline_invariants,
)
from universal_agent.utils.heartbeat_findings_schema import HeartbeatFinding

logger = logging.getLogger(__name__)

DEFAULT_STALE_AGE_MINUTES = 180
STALE_TASK_CRITICAL_THRESHOLD = 3
PARKED_TASK_SAMPLE_LIMIT = 5
STALE_TASK_SAMPLE_LIMIT = 5


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _stale_age_minutes() -> int:
    return max(10, _safe_int(os.getenv("UA_TASK_STALE_MIN_AGE_MINUTES"), DEFAULT_STALE_AGE_MINUTES))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summarize_cron_jobs(jobs: Iterable[Any]) -> List[Dict[str, Any]]:
    """Normalize whatever CronService.list_jobs() returns into dashboard rows.

    Each job is expected to expose a `.to_dict()` method (or already be a
    dict).  We keep this defensive — the watchdog must never crash on a
    schema drift in the cron service.
    """
    rows: List[Dict[str, Any]] = []
    for job in jobs or ():
        try:
            data = job.to_dict() if hasattr(job, "to_dict") else dict(job)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to serialize cron job; skipping", exc_info=True)
            continue
        rows.append(
            {
                "job_id": data.get("job_id") or data.get("id") or "",
                "enabled": bool(data.get("enabled", True)),
                "cron_expr": data.get("cron_expr") or "",
                "last_run_at": data.get("last_run_at") or data.get("last_run") or None,
                "last_outcome": data.get("last_outcome") or data.get("last_status") or None,
                "next_run_at": data.get("next_run_at") or data.get("next_run") or None,
            }
        )
    return rows


def _query_stale_tasks(conn: sqlite3.Connection, stale_minutes: int) -> Dict[str, Any]:
    """Return count + samples of in_progress tasks past the stale age threshold."""
    rows: List[Dict[str, Any]] = []
    count = 0
    try:
        cursor = conn.execute(
            """
            SELECT task_id, source_kind, title, updated_at
            FROM task_hub_items
            WHERE status = 'in_progress'
              AND COALESCE(updated_at, '') < datetime('now', ?)
            ORDER BY updated_at ASC
            """,
            (f"-{stale_minutes} minutes",),
        )
        for row in cursor.fetchall():
            count += 1
            if len(rows) < STALE_TASK_SAMPLE_LIMIT:
                rows.append(
                    {
                        "task_id": row["task_id"],
                        "source_kind": row["source_kind"],
                        "title": row["title"],
                        "updated_at": row["updated_at"],
                    }
                )
    except sqlite3.Error as exc:
        logger.warning("stale-task query failed: %s", exc)
        return {"count": 0, "samples": [], "error": str(exc)}
    return {"count": count, "samples": rows, "threshold_minutes": stale_minutes}


def _query_parked_tasks(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Return count + samples of tasks parked into needs_review."""
    rows: List[Dict[str, Any]] = []
    count = 0
    try:
        cursor = conn.execute(
            """
            SELECT task_id, source_kind, title, updated_at
            FROM task_hub_items
            WHERE status = 'needs_review'
            ORDER BY updated_at DESC
            """
        )
        for row in cursor.fetchall():
            count += 1
            if len(rows) < PARKED_TASK_SAMPLE_LIMIT:
                rows.append(
                    {
                        "task_id": row["task_id"],
                        "source_kind": row["source_kind"],
                        "title": row["title"],
                        "updated_at": row["updated_at"],
                    }
                )
    except sqlite3.Error as exc:
        logger.warning("parked-task query failed: %s", exc)
        return {"count": 0, "samples": [], "error": str(exc)}
    return {"count": count, "samples": rows}


def _derive_overall_status(
    invariants_findings: List[HeartbeatFinding],
    stale_count: int,
    parked_count: int,
) -> str:
    has_critical_invariant = any(f.severity == "critical" for f in invariants_findings)
    has_warn_invariant = any(f.severity == "warn" for f in invariants_findings)
    if has_critical_invariant or stale_count >= STALE_TASK_CRITICAL_THRESHOLD:
        return "critical"
    if has_warn_invariant or stale_count >= 1 or parked_count >= 1:
        return "warn"
    return "ok"


def build_proactive_health_payload(
    *,
    activity_conn: Optional[sqlite3.Connection],
    cron_jobs: Optional[Iterable[Any]] = None,
    csi_db_path: Optional[Path] = None,
    runtime_conn: Optional[sqlite3.Connection] = None,
    stale_minutes: Optional[int] = None,
) -> Dict[str, Any]:
    """Compose the full payload returned by /api/v1/ops/proactive_health.

    Parameters:
      activity_conn: Connection to the activity / task_hub store. None → empty
        stale/parked sections (still serves invariants).
      cron_jobs: Iterable of cron job objects (each .to_dict() compatible) or
        dicts. None → empty crons section.
      csi_db_path: Path to the CSI sqlite DB. Forwarded to invariant probes
        in the context dict.
      runtime_conn: Optional runtime DB connection forwarded to probes.
      stale_minutes: Override the stale threshold (test-only).

    The function never raises on a sub-query failure — it surfaces partial
    state in the response so the watchdog stays useful even when one
    upstream is sick.
    """
    threshold = stale_minutes if stale_minutes is not None else _stale_age_minutes()

    if activity_conn is not None:
        stale = _query_stale_tasks(activity_conn, threshold)
        parked = _query_parked_tasks(activity_conn)
    else:
        stale = {"count": 0, "samples": [], "threshold_minutes": threshold}
        parked = {"count": 0, "samples": []}

    crons = _summarize_cron_jobs(cron_jobs or [])

    # Resolve the canonical artifacts directory once. Imported lazily because
    # it touches env vars + filesystem; we never want this to crash the
    # aggregator on a fresh dev box.
    artifacts_dir: Optional[Path] = None
    try:
        from universal_agent.artifacts import resolve_artifacts_dir
        artifacts_dir = resolve_artifacts_dir()
    except Exception:  # noqa: BLE001
        artifacts_dir = None

    invariant_findings = pipeline_invariants.run_invariants(
        {
            "csi_db_path": csi_db_path,
            "runtime_conn": runtime_conn,
            "activity_conn": activity_conn,
            "artifacts_dir": artifacts_dir,
        }
    )

    overall = _derive_overall_status(
        invariant_findings,
        stale_count=int(stale.get("count") or 0),
        parked_count=int(parked.get("count") or 0),
    )

    return {
        "overall_status": overall,
        "generated_at_utc": _utc_now_iso(),
        "crons": crons,
        "stale_tasks": stale,
        "parked_tasks": parked,
        "invariants": [f.model_dump() for f in invariant_findings],
    }
