"""Proactive Outcome Tracker — Phase 3 feedback loop.

Records the terminal disposition of proactive-sourced tasks, fires implicit
preference signals, triggers auto-investigation for failures, and writes
outcome summaries to long-term memory.

This module is a *passive observer* — it never modifies task status or
assignments.  It is called from the post-action hook in
``perform_task_action`` and must never raise into the caller.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import sqlite3
from typing import Any, Optional
import uuid

logger = logging.getLogger(__name__)

# ── Proactive source kinds that trigger outcome recording ─────────────────

PROACTIVE_SOURCES = frozenset({
    "proactive_signal",
    "reflection",
    "convergence_detection",
    "insight_detection",
    "proactive_codie",
    "tutorial_build",
    "claude_code_demo_task",
    "claude_code_kb_update",
    "heartbeat_remediation",
    "proactive_feedback_continuation",
    "csi",
    "brainstorm",
    "calendar_bridge",
})

# ── Terminal actions that constitute an outcome ───────────────────────────

TERMINAL_ACTIONS = frozenset({
    "complete", "block", "review", "park", "approve",
})

# Actions that indicate failure/issues (trigger auto-investigation)
FAILURE_ACTIONS = frozenset({"block", "review"})

# Implicit preference signal weights by terminal action
_SIGNAL_WEIGHTS: dict[str, float] = {
    "complete": 0.3,
    "approve": 0.5,
    "block": -0.4,
    "review": -0.2,
    "park": -0.1,
    "delegate": 0.0,
}


# ── Schema ────────────────────────────────────────────────────────────────

def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the proactive_outcomes table if it doesn't exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS proactive_outcomes (
            outcome_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            action TEXT NOT NULL,
            terminal_status TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            agent_id TEXT NOT NULL DEFAULT '',
            assignment_count INTEGER NOT NULL DEFAULT 0,
            duration_seconds REAL,
            investigated INTEGER NOT NULL DEFAULT 0,
            investigation_artifact_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_proactive_outcomes_task
            ON proactive_outcomes(task_id);
        CREATE INDEX IF NOT EXISTS idx_proactive_outcomes_action
            ON proactive_outcomes(action, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_proactive_outcomes_source
            ON proactive_outcomes(source_kind, created_at DESC);
        """
    )
    conn.commit()


# ── Core entry point ─────────────────────────────────────────────────────

def record_proactive_outcome(
    conn: sqlite3.Connection,
    *,
    task: dict[str, Any],
    action: str,
    reason: str = "",
    agent_id: str = "",
) -> Optional[dict[str, Any]]:
    """Record the outcome of a proactive task's terminal action.

    Returns the outcome dict, or ``None`` if the task is not proactive-sourced.
    This function is designed to be called from ``perform_task_action`` and must
    never raise — all errors are caught and logged.
    """
    source_kind = str(task.get("source_kind") or "").strip().lower()
    if source_kind not in PROACTIVE_SOURCES:
        return None

    action_norm = str(action or "").strip().lower()
    if action_norm not in TERMINAL_ACTIONS:
        return None

    try:
        return _record_outcome_impl(
            conn,
            task=task,
            source_kind=source_kind,
            action=action_norm,
            reason=reason,
            agent_id=agent_id,
        )
    except Exception as exc:
        task_id = str(task.get("task_id") or "")
        logger.warning("Proactive outcome recording failed for %s: %s", task_id, exc)
        return None


def _record_outcome_impl(
    conn: sqlite3.Connection,
    *,
    task: dict[str, Any],
    source_kind: str,
    action: str,
    reason: str,
    agent_id: str,
) -> dict[str, Any]:
    """Internal implementation — may raise."""
    ensure_schema(conn)

    task_id = str(task.get("task_id") or "").strip()
    outcome_id = f"out_{uuid.uuid4().hex[:16]}"
    now_iso = _now_iso()

    # Compute assignment count and duration
    assignment_count = _count_assignments(conn, task_id)
    duration_seconds = _compute_duration(conn, task_id)

    # Determine terminal status from action
    terminal_status = _action_to_status(action)

    outcome = {
        "outcome_id": outcome_id,
        "task_id": task_id,
        "source_kind": source_kind,
        "action": action,
        "terminal_status": terminal_status,
        "reason": str(reason or "").strip(),
        "agent_id": str(agent_id or "").strip(),
        "assignment_count": assignment_count,
        "duration_seconds": duration_seconds,
        "investigated": 0,
        "investigation_artifact_id": None,
        "created_at": now_iso,
    }

    conn.execute(
        """
        INSERT INTO proactive_outcomes (
            outcome_id, task_id, source_kind, action, terminal_status,
            reason, agent_id, assignment_count, duration_seconds,
            investigated, investigation_artifact_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            outcome_id, task_id, source_kind, action, terminal_status,
            outcome["reason"], outcome["agent_id"], assignment_count,
            duration_seconds, 0, None, now_iso,
        ),
    )
    conn.commit()

    logger.info(
        "Proactive outcome recorded: task=%s action=%s source=%s duration=%.1fs",
        task_id, action, source_kind, duration_seconds or 0.0,
    )

    # Fire implicit preference signal (non-critical)
    _fire_implicit_preference_signal(conn, task=task, action=action)

    # Auto-investigation for failure actions (non-critical)
    if action in FAILURE_ACTIONS and _auto_investigate_enabled():
        investigation = _trigger_auto_investigation(conn, task=task, outcome=outcome)
        if investigation:
            outcome["investigated"] = 1
            outcome["investigation_artifact_id"] = investigation.get("artifact_id")
            conn.execute(
                """
                UPDATE proactive_outcomes
                SET investigated = 1, investigation_artifact_id = ?
                WHERE outcome_id = ?
                """,
                (investigation.get("artifact_id", ""), outcome_id),
            )
            conn.commit()

    # Write outcome to memory (non-critical)
    if _outcome_memory_enabled():
        _write_outcome_to_memory(task=task, outcome=outcome)

    # Store an auditable recap for dashboard history (non-critical).
    _store_work_recap(conn, task=task, action=action, reason=reason)

    return outcome


def _store_work_recap(
    conn: sqlite3.Connection,
    *,
    task: dict[str, Any],
    action: str,
    reason: str,
) -> None:
    try:
        from universal_agent.services.proactive_work_recap import upsert_recap_for_task

        upsert_recap_for_task(conn, task=task, action=action, reason=reason)
    except Exception as exc:
        task_id = str(task.get("task_id") or "")
        logger.warning("Proactive work recap failed for %s: %s", task_id, exc)


# ── Statistics & Queries ─────────────────────────────────────────────────

def get_outcome_stats(
    conn: sqlite3.Connection,
    *,
    window_hours: int = 168,
) -> dict[str, Any]:
    """Aggregate outcome statistics over a time window (default 7 days)."""
    ensure_schema(conn)
    cutoff = _cutoff_iso(max(1, int(window_hours)))

    # Total outcomes in window
    total_row = conn.execute(
        "SELECT COUNT(*) AS total FROM proactive_outcomes WHERE created_at >= ?",
        (cutoff,),
    ).fetchone()
    total = int(total_row["total"] or 0) if total_row else 0

    # By action
    action_rows = conn.execute(
        """
        SELECT action, COUNT(*) AS count
        FROM proactive_outcomes
        WHERE created_at >= ?
        GROUP BY action
        ORDER BY count DESC
        """,
        (cutoff,),
    ).fetchall()
    by_action = {str(r["action"]): int(r["count"]) for r in action_rows}

    # By source kind
    source_rows = conn.execute(
        """
        SELECT source_kind, COUNT(*) AS count
        FROM proactive_outcomes
        WHERE created_at >= ?
        GROUP BY source_kind
        ORDER BY count DESC
        """,
        (cutoff,),
    ).fetchall()
    by_source = {str(r["source_kind"]): int(r["count"]) for r in source_rows}

    # Success rate
    success_count = sum(by_action.get(a, 0) for a in ("complete", "approve"))
    failure_count = sum(by_action.get(a, 0) for a in ("block", "review"))
    success_rate = round(success_count / total, 3) if total > 0 else 0.0

    # Average duration
    dur_row = conn.execute(
        """
        SELECT AVG(duration_seconds) AS avg_dur, MAX(duration_seconds) AS max_dur
        FROM proactive_outcomes
        WHERE created_at >= ? AND duration_seconds IS NOT NULL
        """,
        (cutoff,),
    ).fetchone()
    avg_duration = round(float(dur_row["avg_dur"] or 0), 1) if dur_row else 0.0
    max_duration = round(float(dur_row["max_dur"] or 0), 1) if dur_row else 0.0

    # Investigation count
    inv_row = conn.execute(
        "SELECT COUNT(*) AS count FROM proactive_outcomes WHERE created_at >= ? AND investigated = 1",
        (cutoff,),
    ).fetchone()
    investigated_count = int(inv_row["count"] or 0) if inv_row else 0

    # Top failure reasons
    reason_rows = conn.execute(
        """
        SELECT reason, COUNT(*) AS count
        FROM proactive_outcomes
        WHERE created_at >= ? AND action IN ('block', 'review') AND reason != ''
        GROUP BY reason
        ORDER BY count DESC
        LIMIT 5
        """,
        (cutoff,),
    ).fetchall()
    top_failure_reasons = [
        {"reason": str(r["reason"]), "count": int(r["count"])}
        for r in reason_rows
    ]

    return {
        "window_hours": window_hours,
        "total": total,
        "by_action": by_action,
        "by_source_kind": by_source,
        "success_count": success_count,
        "failure_count": failure_count,
        "success_rate": success_rate,
        "avg_duration_seconds": avg_duration,
        "max_duration_seconds": max_duration,
        "investigated_count": investigated_count,
        "top_failure_reasons": top_failure_reasons,
    }


def get_recent_outcomes(
    conn: sqlite3.Connection,
    *,
    limit: int = 20,
    action_filter: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Get recent outcome records for API consumption."""
    ensure_schema(conn)
    clean_limit = max(1, min(int(limit), 100))

    if action_filter:
        rows = conn.execute(
            """
            SELECT * FROM proactive_outcomes
            WHERE action = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (str(action_filter).strip().lower(), clean_limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM proactive_outcomes
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (clean_limit,),
        ).fetchall()

    return [dict(row) for row in rows]


# ── Implicit Preference Signals ──────────────────────────────────────────

def _fire_implicit_preference_signal(
    conn: sqlite3.Connection,
    *,
    task: dict[str, Any],
    action: str,
) -> None:
    """Record an implicit preference signal based on task outcome."""
    weight = _SIGNAL_WEIGHTS.get(action, 0.0)
    if weight == 0.0:
        return

    try:
        from universal_agent.services.proactive_preferences import (
            ensure_schema as pref_ensure_schema,
            rebuild_preference_snapshot,
        )

        pref_ensure_schema(conn)
        now_iso = _now_iso()

        # Build signal keys from task metadata (same pattern as proactive_preferences)
        keys = _build_signal_keys(task)
        for key in keys:
            conn.execute(
                """
                INSERT INTO proactive_preference_signals (
                    artifact_id, signal_key, signal_type, weight, score, text,
                    created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(task.get("task_id") or ""),
                    key,
                    "implicit_outcome",
                    weight,
                    None,
                    f"Outcome: {action}",
                    now_iso,
                    _json_dumps({
                        "source_kind": task.get("source_kind"),
                        "action": action,
                        "implicit": True,
                    }),
                ),
            )
        conn.commit()

        # Rebuild the preference snapshot with new signals
        rebuild_preference_snapshot(conn)
        logger.debug("Implicit preference signal: %s weight=%.2f keys=%s", action, weight, keys)
    except Exception as exc:
        logger.warning("Failed to fire implicit preference signal: %s", exc)


def _build_signal_keys(task: dict[str, Any]) -> list[str]:
    """Build preference signal keys from task metadata."""
    keys: list[str] = []
    source_kind = str(task.get("source_kind") or "").strip().lower()
    if source_kind:
        keys.append(f"source:{source_kind}")

    project_key = str(task.get("project_key") or "").strip().lower()
    if project_key:
        keys.append(f"project:{project_key}")

    for label in (task.get("labels") or []):
        clean = str(label or "").strip().lower()
        if clean:
            keys.append(f"topic:{clean}")

    metadata = task.get("metadata") or {}
    if isinstance(metadata, dict):
        for tag_key in ("primary_topic", "category", "content_type"):
            value = str(metadata.get(tag_key) or "").strip().lower()
            if value:
                keys.append(f"topic:{value}")

    return sorted(set(keys))


# ── Auto-Investigation ───────────────────────────────────────────────────

def _trigger_auto_investigation(
    conn: sqlite3.Connection,
    *,
    task: dict[str, Any],
    outcome: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Trigger auto-investigation for a failed proactive task."""
    try:
        from universal_agent.services.proactive_auto_investigator import (
            investigate_proactive_failure,
        )
        return investigate_proactive_failure(conn, task=task, outcome=outcome)
    except Exception as exc:
        logger.warning(
            "Auto-investigation failed for task %s: %s",
            task.get("task_id"), exc,
        )
        return None


# ── Memory Integration ───────────────────────────────────────────────────

def _write_outcome_to_memory(
    *,
    task: dict[str, Any],
    outcome: dict[str, Any],
    diagnostic: Optional[dict[str, Any]] = None,
) -> bool:
    """Write outcome summary to long-term memory for future retrieval."""
    try:
        from universal_agent.memory.orchestrator import (
            MemoryOrchestrator,
            _resolve_workspace_dir,
        )

        workspace = _resolve_workspace_dir(None)
        mo = MemoryOrchestrator(workspace)

        action = str(outcome.get("action") or "unknown")
        task_title = str(task.get("title") or "untitled")
        source_kind = str(task.get("source_kind") or "unknown")
        duration = outcome.get("duration_seconds")
        duration_str = f"{duration:.0f}s" if duration is not None else "unknown"

        if action in ("complete", "approve"):
            content = (
                f"Proactive task completed successfully: {task_title}\n"
                f"Source: {source_kind} | Duration: {duration_str}\n"
                f"This type of proactive work was productive."
            )
            tags = ["proactive_outcome", "success", f"source:{source_kind}"]
        else:
            reason = str(outcome.get("reason") or "unknown")
            content = (
                f"Proactive task failed/blocked: {task_title}\n"
                f"Source: {source_kind} | Action: {action} | Reason: {reason}\n"
            )
            if diagnostic and diagnostic.get("summary"):
                content += f"Diagnostic: {diagnostic['summary']}\n"
            content += "This type of proactive work had issues — consider adjusting dispatch."
            tags = ["proactive_outcome", "failure", f"source:{source_kind}"]

        entry = mo.write(
            content=content,
            source="proactive_outcome",
            session_id=None,
            tags=tags,
            importance=0.6,
        )
        if entry:
            logger.debug("Outcome memory written for task %s", task.get("task_id"))
        return entry is not None
    except Exception as exc:
        logger.warning("Failed to write outcome to memory: %s", exc)
        return False


# ── Helpers ──────────────────────────────────────────────────────────────

def _count_assignments(conn: sqlite3.Connection, task_id: str) -> int:
    """Count all assignments for a given task."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM task_hub_assignments WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return int(row["count"] or 0) if row else 0
    except Exception:
        return 0


def _compute_duration(conn: sqlite3.Connection, task_id: str) -> Optional[float]:
    """Compute seconds from first assignment start to now."""
    try:
        row = conn.execute(
            "SELECT MIN(started_at) AS first_start FROM task_hub_assignments WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if not row or not row["first_start"]:
            return None
        first_start = datetime.fromisoformat(
            str(row["first_start"]).replace("Z", "+00:00")
        )
        if first_start.tzinfo is None:
            first_start = first_start.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (now - first_start).total_seconds())
    except Exception:
        return None


_STATUS_MAP = {
    "complete": "completed",
    "approve": "completed",
    "block": "blocked",
    "review": "pending_review",
    "park": "parked",
}


def _action_to_status(action: str) -> str:
    """Map an action to its resulting task status."""
    return _STATUS_MAP.get(action, action)


def _auto_investigate_enabled() -> bool:
    """Check if auto-investigation is enabled via feature flag."""
    return os.getenv("UA_PROACTIVE_AUTO_INVESTIGATE", "true").strip().lower() in (
        "true", "1", "yes",
    )


def _outcome_memory_enabled() -> bool:
    """Check if outcome memory writing is enabled via feature flag."""
    return os.getenv("UA_PROACTIVE_OUTCOME_MEMORY", "true").strip().lower() in (
        "true", "1", "yes",
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cutoff_iso(hours: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
