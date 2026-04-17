"""
proactive_advisor.py — Deterministic morning-report builder for the heartbeat cycle.

This module assembles a structured snapshot of Task Hub state for the heartbeat
agent.  All logic is pure Python (no LLM calls) — the LLM only sees the
formatted report text as additional prompt context.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from universal_agent import task_hub

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Staleness thresholds
# ---------------------------------------------------------------------------
DEFAULT_STALE_IN_PROGRESS_HOURS = 24
DEFAULT_STALE_BRAINSTORM_HOURS = 48


def _parse_iso_age_hours(iso_str: str | None) -> float | None:
    """Return the age of an ISO timestamp in hours, or None if unparseable."""
    if not iso_str:
        return None
    try:
        text = iso_str.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Morning Report Builder
# ---------------------------------------------------------------------------

def build_morning_report(
    conn: sqlite3.Connection,
    *,
    stale_brainstorm_hours: int = DEFAULT_STALE_BRAINSTORM_HOURS,
    stale_in_progress_hours: int = DEFAULT_STALE_IN_PROGRESS_HOURS,
) -> dict[str, Any]:
    """Assemble a deterministic morning snapshot of Task Hub state.

    Returns a structured dict with counts, brainstorm status, stale items,
    and a pre-formatted ``report_text`` for prompt injection.
    """
    task_hub.ensure_schema(conn)

    # --- Active task counts ---
    active_rows = conn.execute(
        "SELECT * FROM task_hub_items WHERE status NOT IN ('done', 'parked', 'cancelled')"
    ).fetchall()
    active_items = [dict(r) for r in active_rows]

    # --- Brainstorm tasks (have refinement_stage) ---
    brainstorm_tasks = []
    for item in active_items:
        stage = item.get("refinement_stage")
        if not stage:
            continue
        task_id = str(item.get("task_id") or "")
        # Count pending questions for this task
        q_rows = conn.execute(
            "SELECT COUNT(*) as cnt FROM task_hub_question_queue "
            "WHERE task_id = ? AND answered = 0 AND (expires_at IS NULL OR expires_at > ?)",
            (task_id, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
        pending_q = int(q_rows[0]) if q_rows else 0
        age_hours = _parse_iso_age_hours(item.get("updated_at"))
        brainstorm_tasks.append({
            "task_id": task_id,
            "title": str(item.get("title") or "")[:80],
            "stage": stage,
            "pending_questions": pending_q,
            "stale_hours": round(age_hours, 1) if age_hours else None,
            "is_stale": (age_hours or 0) >= stale_brainstorm_hours,
        })

    # --- Stale in-progress (non-brainstorm) ---
    stale_in_progress = []
    for item in active_items:
        if item.get("refinement_stage"):
            continue  # already counted above
        if item.get("status") != "in_progress":
            continue
        age_hours = _parse_iso_age_hours(item.get("updated_at"))
        if age_hours and age_hours >= stale_in_progress_hours:
            stale_in_progress.append({
                "task_id": str(item.get("task_id") or ""),
                "title": str(item.get("title") or "")[:80],
                "stale_hours": round(age_hours, 1),
            })

    # --- Overdue scheduled tasks ---
    try:
        overdue = task_hub.list_due_scheduled_tasks(conn, limit=10)
    except Exception:
        overdue = []

    # --- Unanswered questions ---
    pending_questions = task_hub.list_pending_questions(conn, limit=50)
    unanswered_count = len(pending_questions)

    # --- Expiring questions (within 30 min) ---
    expiring = task_hub.list_expiring_questions(conn, within_minutes=30)

    report = {
        "total_active": len(active_items),
        "brainstorm_tasks": brainstorm_tasks,
        "stale_brainstorm_count": sum(1 for b in brainstorm_tasks if b.get("is_stale")),
        "overdue_scheduled": overdue,
        "stale_in_progress": stale_in_progress,
        "unanswered_questions_count": unanswered_count,
        "expiring_questions_count": len(expiring),
        "report_text": "",
    }
    report["report_text"] = format_morning_report_prompt(report)
    return report


# ---------------------------------------------------------------------------
# Prompt Formatting
# ---------------------------------------------------------------------------

def format_morning_report_prompt(report: dict[str, Any]) -> str:
    """Format the morning report dict into a markdown prompt section."""
    lines = [
        "## 📋 Morning Report — Task Hub Snapshot",
        f"- **Active tasks**: {report.get('total_active', 0)}",
        f"- **Unanswered questions**: {report.get('unanswered_questions_count', 0)}",
        f"- **Expiring soon**: {report.get('expiring_questions_count', 0)}",
    ]

    brainstorms = report.get("brainstorm_tasks") or []
    if brainstorms:
        lines.append("")
        lines.append("### Brainstorm Tasks")
        lines.append("| Task | Stage | Questions | Stale? |")
        lines.append("|------|-------|-----------|--------|")
        for b in brainstorms:
            stale_flag = "⚠️ YES" if b.get("is_stale") else "no"
            lines.append(
                f"| {b['title']} | {b['stage']} | {b['pending_questions']} pending | {stale_flag} |"
            )

    stale = report.get("stale_in_progress") or []
    if stale:
        lines.append("")
        lines.append("### Stale In-Progress Tasks")
        for s in stale:
            lines.append(f"- **{s['title']}** — stale for {s['stale_hours']}h")

    overdue = report.get("overdue_scheduled") or []
    if overdue:
        lines.append("")
        lines.append(f"### Overdue Scheduled Tasks ({len(overdue)})")
        for o in overdue:
            lines.append(f"- {o.get('title', o.get('task_id', 'unknown'))}")

    lines.append("")
    lines.append(
        "INSTRUCTIONS: Review the above snapshot. For stale brainstorm tasks, consider "
        "triggering a refinement cycle. For expiring questions, proactively re-ask the user. "
        "For stale in-progress items, check if they should be parked or completed."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Brainstorm Context for Heartbeat Prompt
# ---------------------------------------------------------------------------

def build_brainstorm_context(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Build brainstorm context data for heartbeat prompt injection.

    Returns a list of dicts with task_id, title, stage, pending_questions.
    """
    brainstorms = task_hub.list_brainstorm_tasks(conn, limit=20)
    if not brainstorms:
        return []

    context = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for item in brainstorms:
        task_id = str(item.get("task_id") or "")
        q_rows = conn.execute(
            "SELECT COUNT(*) as cnt FROM task_hub_question_queue "
            "WHERE task_id = ? AND answered = 0 AND (expires_at IS NULL OR expires_at > ?)",
            (task_id, now_iso),
        ).fetchone()
        pending_q = int(q_rows[0]) if q_rows else 0
        context.append({
            "task_id": task_id,
            "title": str(item.get("title") or "")[:80],
            "stage": str(item.get("refinement_stage") or ""),
            "pending_questions": pending_q,
        })
    return context


def format_brainstorm_context_prompt(context: list[dict[str, Any]]) -> str:
    """Format brainstorm context list into a prompt section."""
    if not context:
        return ""
    lines = [
        "## Brainstorm Tasks In Progress",
        "| Task | Stage | Pending Questions |",
        "|------|-------|-------------------|",
    ]
    for c in context:
        lines.append(f"| {c['title']} | {c['stage']} | {c['pending_questions']} unanswered |")
    lines.append("")
    lines.append(
        "INSTRUCTIONS: For brainstorm tasks with pending questions nearing timeout, "
        "proactively re-ask the user. For tasks in early stages with no recent activity, "
        "consider triggering a refinement cycle."
    )
    return "\n".join(lines)
