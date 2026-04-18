"""
proactive_intelligence_report.py — 3x daily hybrid intelligence reports.

Combines deterministic Python data gathering (pipeline stats, budget, utilization)
with an LLM reasoning pass that interprets results and provides actionable
recommendations.  Delivered via email AND stored for dashboard display.

Reports run at 7 AM, 12 PM, and 4 PM CT via cron entries.

Architecture:
  1. gather_pipeline_stats(conn)  → deterministic Python stats
  2. _call_reasoning_llm(stats)   → LLM analysis/recommendations
  3. compose_intelligence_report() → combine into report dict
  4. deliver_intelligence_report() → email + store for dashboard
"""

from __future__ import annotations

import html
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from universal_agent import task_hub
from universal_agent import proactive_signals
from universal_agent.services.proactive_budget import (
    get_daily_proactive_count,
    get_budget_remaining,
    _parse_int_env,
    DEFAULT_DAILY_BUDGET,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_REPORT_TABLE = "proactive_intelligence_reports"
_UTILIZATION_TABLE = "proactive_utilization_samples"

PROACTIVE_SOURCE_KINDS = ("proactive_signal", "reflection")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def ensure_report_schema(conn: sqlite3.Connection) -> None:
    """Create tables for reports and utilization samples if they don't exist."""
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {_REPORT_TABLE} (
            report_id       TEXT PRIMARY KEY,
            period          TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            stats_json      TEXT NOT NULL DEFAULT '{{}}',
            analysis        TEXT NOT NULL DEFAULT '',
            email_message_id TEXT DEFAULT '',
            email_thread_id  TEXT DEFAULT '',
            created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        )
    """)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {_UTILIZATION_TABLE} (
            sample_id       TEXT PRIMARY KEY,
            active_slots    INTEGER NOT NULL DEFAULT 0,
            max_slots       INTEGER NOT NULL DEFAULT 2,
            queue_depth     INTEGER NOT NULL DEFAULT 0,
            sampled_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        )
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# 1. Data Gathering (deterministic Python)
# ---------------------------------------------------------------------------

def gather_pipeline_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather proactive pipeline stats from Task Hub and Signal Cards.

    Returns a structured dict with:
      - proactive_tasks: open/completed/failed/total counts + by_source breakdown
      - budget: used/remaining/daily_limit
      - signal_cards: pending/promoted counts
      - utilization: avg occupancy, peak, queue depth
      - timestamp, period
    """
    task_hub.ensure_schema(conn)
    ensure_report_schema(conn)

    # ── Proactive task status counts ──────────────────────────────────
    proactive_status_counts: dict[str, int] = {"open": 0, "completed": 0, "failed": 0}
    proactive_total = 0
    proactive_by_source: dict[str, int] = {}

    try:
        rows = conn.execute(
            """
            SELECT status, source_kind, COUNT(*) AS c
            FROM task_hub_items
            WHERE source_kind IN (?, ?)
            GROUP BY status, source_kind
            """,
            PROACTIVE_SOURCE_KINDS,
        ).fetchall()

        for row in rows:
            status = str(row["status"] or "open").strip().lower()
            source_kind = str(row["source_kind"] or "unknown")
            count = int(row["c"] or 0)
            proactive_total += count

            # Map statuses to our buckets
            if status in ("completed",):
                proactive_status_counts["completed"] += count
            elif status in ("parked", "failed", "cancelled"):
                proactive_status_counts["failed"] += count
            else:
                proactive_status_counts["open"] += count

            proactive_by_source[source_kind] = proactive_by_source.get(source_kind, 0) + count
    except Exception as exc:
        logger.warning("Failed to gather proactive task counts: %s", exc)

    # ── Budget consumption ────────────────────────────────────────────
    budget_used = get_daily_proactive_count(conn)
    budget_remaining = get_budget_remaining(conn)
    budget_limit = _parse_int_env("UA_PROACTIVE_DAILY_BUDGET", DEFAULT_DAILY_BUDGET)

    # ── Signal card counts ────────────────────────────────────────────
    signal_pending = 0
    signal_promoted = 0
    try:
        proactive_signals.ensure_schema(conn)
        pending_rows = conn.execute(
            "SELECT COUNT(*) AS c FROM proactive_signal_cards WHERE status = 'pending'"
        ).fetchone()
        signal_pending = int(pending_rows["c"]) if pending_rows else 0

        promoted_rows = conn.execute(
            "SELECT COUNT(*) AS c FROM proactive_signal_cards WHERE status = 'promoted'"
        ).fetchone()
        signal_promoted = int(promoted_rows["c"]) if promoted_rows else 0
    except Exception as exc:
        logger.warning("Failed to gather signal card counts: %s", exc)

    # ── Utilization stats ─────────────────────────────────────────────
    utilization = get_utilization_stats(conn, window_hours=24)

    now = datetime.now(timezone.utc)
    return {
        "proactive_tasks": {
            "open": proactive_status_counts["open"],
            "completed": proactive_status_counts["completed"],
            "failed": proactive_status_counts["failed"],
            "total": proactive_total,
            "by_source": proactive_by_source,
        },
        "budget": {
            "used": budget_used,
            "remaining": budget_remaining,
            "daily_limit": budget_limit,
        },
        "signal_cards": {
            "pending": signal_pending,
            "promoted": signal_promoted,
        },
        "utilization": utilization,
        "timestamp": now.isoformat(),
        "period": _current_period_label(),
    }


# ---------------------------------------------------------------------------
# 2. LLM Reasoning Pass
# ---------------------------------------------------------------------------

async def _call_reasoning_llm(stats: dict[str, Any], period: str) -> str:
    """Call an LLM to interpret pipeline stats and provide actionable analysis.

    This is the "colleague, not a dashboard" reasoning pass that Kevin requested.
    Uses Gemini Flash for fast, judgment-oriented analysis.
    """
    try:
        from google import genai
    except ImportError:
        logger.warning("google-genai not available; returning static analysis")
        return _fallback_analysis(stats, period)

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
    if not api_key:
        logger.warning("No Gemini API key available; returning static analysis")
        return _fallback_analysis(stats, period)

    stats_json = json.dumps(stats, indent=2, default=str)
    prompt = f"""You are Simone, Kevin's AI chief of staff, briefing him on the autonomous proactive pipeline.

Here are the pipeline stats since the last report:

```json
{stats_json}
```

This is the {period} briefing. Write a concise, conversational analysis (2-4 paragraphs):

1. **What happened**: Summarize proactive task activity — what was created, completed, or failed.
2. **System health**: Comment on budget utilization and system load. Is the system being used enough, or is it stressed?
3. **What to do next**: Provide 1-3 actionable recommendations. Think like a colleague, not a dashboard.
   - If tasks failed, suggest investigation priorities.
   - If the system is under-utilized, suggest topics or types of work to explore.
   - If the system is over-utilized, suggest throttling strategies.

Be specific. Reference actual numbers. Don't hedge — give direct advice.
Keep it under 300 words.
"""

    try:
        client = genai.Client(api_key=api_key)
        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        text = response.text or ""
        if text.strip():
            return text.strip()
        return _fallback_analysis(stats, period)
    except Exception as exc:
        logger.warning("LLM reasoning call failed: %s", exc)
        return _fallback_analysis(stats, period)


def _fallback_analysis(stats: dict[str, Any], period: str) -> str:
    """Generate a basic analysis when LLM is unavailable."""
    tasks = stats.get("proactive_tasks", {})
    budget = stats.get("budget", {})
    return (
        f"Proactive pipeline {period} summary: "
        f"{tasks.get('completed', 0)} tasks completed, "
        f"{tasks.get('open', 0)} open, "
        f"{tasks.get('failed', 0)} failed. "
        f"Budget: {budget.get('used', 0)}/{budget.get('daily_limit', 10)} used today. "
        f"LLM analysis unavailable — review stats above for detailed assessment."
    )


# ---------------------------------------------------------------------------
# 3. Report Composition
# ---------------------------------------------------------------------------

async def compose_intelligence_report(
    conn: sqlite3.Connection,
    *,
    period: str | None = None,
) -> dict[str, Any]:
    """Compose a full intelligence report: stats + LLM reasoning.

    Returns dict with: stats, analysis, period, timestamp.
    """
    stats = gather_pipeline_stats(conn)
    effective_period = period or stats.get("period", "unknown")
    stats["period"] = effective_period

    analysis = await _call_reasoning_llm(stats, effective_period)

    return {
        "stats": stats,
        "analysis": analysis,
        "period": effective_period,
        "timestamp": stats["timestamp"],
    }


def format_report_email(report: dict[str, Any]) -> tuple[str, str, str]:
    """Format report into (subject, text_body, html_body)."""
    period = str(report.get("period", "")).strip()
    period_label = period.capitalize() if period else "Update"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    subject = f"[UA Proactive] {period_label} Intelligence Report — {today}"

    stats = report.get("stats", {})
    tasks = stats.get("proactive_tasks", {})
    budget = stats.get("budget", {})
    signals = stats.get("signal_cards", {})
    utilization = stats.get("utilization", {})
    analysis = report.get("analysis", "")

    lines = [
        f"Proactive Pipeline — {period_label} Report",
        f"Generated: {report.get('timestamp', today)}",
        "",
        "═══ Pipeline Activity ═══",
        f"  Open tasks:      {tasks.get('open', 0)}",
        f"  Completed:       {tasks.get('completed', 0)}",
        f"  Failed/Parked:   {tasks.get('failed', 0)}",
        f"  Total proactive: {tasks.get('total', 0)}",
        "",
        "═══ Budget ═══",
        f"  Used today:  {budget.get('used', 0)} / {budget.get('daily_limit', 10)}",
        f"  Remaining:   {budget.get('remaining', 0)}",
        "",
        "═══ Signal Cards ═══",
        f"  Pending:   {signals.get('pending', 0)}",
        f"  Promoted:  {signals.get('promoted', 0)}",
        "",
    ]

    if utilization.get("sample_count", 0) > 0:
        lines.extend([
            "═══ System Utilization ═══",
            f"  Avg occupancy:   {utilization.get('avg_occupancy_pct', 0):.0f}%",
            f"  Peak slots:      {utilization.get('peak_occupancy_slots', 0)}",
            f"  Avg queue depth: {utilization.get('avg_queue_depth', 0):.1f}",
            f"  Samples:         {utilization.get('sample_count', 0)}",
            "",
        ])

    if analysis:
        lines.extend([
            "═══ Analysis & Recommendations ═══",
            "",
            analysis,
            "",
        ])

    text_body = "\n".join(lines)

    # Build HTML
    escaped = html.escape(text_body).replace("\n", "<br>")
    html_body = f"""<html><body>
<div style="font-family: 'Inter', -apple-system, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
<h2 style="color: #1a1a2e;">🤖 Proactive Pipeline — {period_label} Report</h2>
<pre style="background: #f8f9fa; padding: 16px; border-radius: 8px; font-size: 13px; overflow-x: auto;">{escaped}</pre>
</div>
</body></html>"""

    return subject, text_body, html_body


# ---------------------------------------------------------------------------
# 4. Delivery (Email + Dashboard Store)
# ---------------------------------------------------------------------------

async def deliver_intelligence_report(
    *,
    conn: sqlite3.Connection,
    mail_service: Any,
    recipient: str,
    period: str | None = None,
) -> dict[str, Any]:
    """Compose, deliver via email, and store report for dashboard.

    Returns dict with: report_id, email_sent, stored_for_dashboard, etc.
    """
    ensure_report_schema(conn)
    report = await compose_intelligence_report(conn, period=period)

    subject, text_body, html_body = format_report_email(report)
    report_id = f"rpt-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{report.get('period', 'x')}-{uuid.uuid4().hex[:8]}"

    # ── Email delivery ────────────────────────────────────────────────
    email_sent = False
    email_message_id = ""
    email_thread_id = ""
    try:
        result = await mail_service.send_email(
            to=recipient,
            subject=subject,
            text=text_body,
            html=html_body,
            force_send=True,
            require_approval=False,
        )
        email_sent = True
        email_message_id = str((result or {}).get("message_id", ""))
        email_thread_id = str((result or {}).get("thread_id", ""))
    except Exception as exc:
        logger.error("Failed to send intelligence report email: %s", exc)

    # ── Dashboard store ───────────────────────────────────────────────
    stored = False
    try:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {_REPORT_TABLE}
            (report_id, period, timestamp, stats_json, analysis, email_message_id, email_thread_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                report.get("period", "unknown"),
                report.get("timestamp", datetime.now(timezone.utc).isoformat()),
                json.dumps(report.get("stats", {}), default=str),
                report.get("analysis", ""),
                email_message_id,
                email_thread_id,
            ),
        )
        conn.commit()
        stored = True
    except Exception as exc:
        logger.error("Failed to store intelligence report: %s", exc)

    return {
        "report_id": report_id,
        "email_sent": email_sent,
        "stored_for_dashboard": stored,
        "period": report.get("period", "unknown"),
        "timestamp": report.get("timestamp", ""),
    }


# ---------------------------------------------------------------------------
# Utilization Tracking
# ---------------------------------------------------------------------------

def record_utilization_sample(
    conn: sqlite3.Connection,
    *,
    active_slots: int,
    max_slots: int,
    queue_depth: int,
) -> None:
    """Record a point-in-time utilization sample (called from heartbeat loop)."""
    ensure_report_schema(conn)
    sample_id = f"util-{uuid.uuid4().hex[:12]}"
    conn.execute(
        f"""
        INSERT INTO {_UTILIZATION_TABLE} (sample_id, active_slots, max_slots, queue_depth, sampled_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            sample_id,
            active_slots,
            max(1, max_slots),
            queue_depth,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def get_utilization_stats(conn: sqlite3.Connection, *, window_hours: int = 24) -> dict[str, Any]:
    """Compute utilization statistics from recent samples.

    Returns:
        dict with: sample_count, avg_occupancy_pct, peak_occupancy_slots, avg_queue_depth
    """
    ensure_report_schema(conn)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()

    rows = conn.execute(
        f"""
        SELECT active_slots, max_slots, queue_depth
        FROM {_UTILIZATION_TABLE}
        WHERE sampled_at >= ?
        ORDER BY sampled_at DESC
        """,
        (cutoff,),
    ).fetchall()

    if not rows:
        return {
            "sample_count": 0,
            "avg_occupancy_pct": 0,
            "peak_occupancy_slots": 0,
            "avg_queue_depth": 0,
        }

    total_occupancy_pct = 0.0
    peak_slots = 0
    total_queue_depth = 0
    count = len(rows)

    for row in rows:
        active = int(row["active_slots"] or 0)
        max_s = max(1, int(row["max_slots"] or 1))
        q_depth = int(row["queue_depth"] or 0)

        occupancy_pct = (active / max_s) * 100.0
        total_occupancy_pct += occupancy_pct
        peak_slots = max(peak_slots, active)
        total_queue_depth += q_depth

    return {
        "sample_count": count,
        "avg_occupancy_pct": round(total_occupancy_pct / count, 1),
        "peak_occupancy_slots": peak_slots,
        "avg_queue_depth": round(total_queue_depth / count, 1),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_period_label() -> str:
    """Determine current report period based on CT hour."""
    try:
        import zoneinfo
        ct = datetime.now(zoneinfo.ZoneInfo("America/Chicago"))
    except Exception:
        ct = datetime.now(timezone.utc)

    hour = ct.hour
    if hour < 10:
        return "morning"
    elif hour < 14:
        return "noon"
    else:
        return "afternoon"


def get_latest_reports(conn: sqlite3.Connection, *, limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve the most recent intelligence reports for dashboard display."""
    ensure_report_schema(conn)
    rows = conn.execute(
        f"""
        SELECT report_id, period, timestamp, stats_json, analysis,
               email_message_id, email_thread_id, created_at
        FROM {_REPORT_TABLE}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    reports = []
    for row in rows:
        stats = {}
        try:
            stats = json.loads(row["stats_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            pass

        reports.append({
            "report_id": str(row["report_id"] or ""),
            "period": str(row["period"] or ""),
            "timestamp": str(row["timestamp"] or ""),
            "stats": stats,
            "analysis": str(row["analysis"] or ""),
            "email_message_id": str(row["email_message_id"] or ""),
            "email_thread_id": str(row["email_thread_id"] or ""),
            "created_at": str(row["created_at"] or ""),
        })

    return reports
