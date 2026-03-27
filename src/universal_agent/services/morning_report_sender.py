"""
morning_report_sender.py — Scheduled 7 AM morning report email sender.

Sends a daily digest email to the operator (Kevin) summarizing:
  - Overnight autonomous activity (reflection engine work)
  - Current Task Hub state (open, in-progress, blocked, stale)
  - Brainstorm pipeline status
  - Completed tasks since the last report
  - Recommendations and open questions

The sender is designed to be called by the gateway cron service or by the
heartbeat service on the first morning tick.  It is NOT an LLM call — it
builds a deterministic report from Task Hub state and overnight session logs.

Usage from gateway_server:
    sender = MorningReportSender(
        agentmail_service=_agentmail_service,
        task_hub_db_path=_hub_db(),
    )
    await sender.send_if_due()
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from universal_agent import task_hub
from universal_agent.services.proactive_advisor import (
    build_morning_report,
    format_morning_report_prompt,
)
from universal_agent.services.reflection_engine import (
    _get_nightly_task_count,
    _get_recent_completions,
    _get_stalled_brainstorms,
    _get_open_task_count,
    _parse_int_env,
    DEFAULT_MAX_NIGHTLY_TASKS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_REPORT_HOUR = 7       # 7 AM local time
DEFAULT_REPORT_MINUTE = 0
_LAST_REPORT_SENT_KEY = "morning_report_last_sent_date"


def _get_recipient_email() -> str:
    """Resolve the operator email to send the morning report to."""
    return (
        os.getenv("UA_MORNING_REPORT_EMAIL")
        or os.getenv("UA_PRIMARY_EMAIL")
        or os.getenv("UA_NOTIFICATION_EMAIL")
        or ""
    ).strip()


def _get_report_hour() -> int:
    return _parse_int_env("UA_MORNING_REPORT_HOUR", DEFAULT_REPORT_HOUR)


def _is_morning_report_enabled() -> bool:
    raw = (os.getenv("UA_MORNING_REPORT_ENABLED") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    # Default: enabled if reflection engine or autonomous heartbeat is enabled
    auto_raw = (os.getenv("UA_HEARTBEAT_AUTONOMOUS_ENABLED") or "").strip().lower()
    return auto_raw not in {"0", "false", "no", "off"}


def _already_sent_today(conn: sqlite3.Connection) -> bool:
    """Check if the morning report has already been sent today."""
    task_hub.ensure_schema(conn)
    setting = task_hub._get_setting(conn, _LAST_REPORT_SENT_KEY)
    if not setting:
        return False
    sent_date = str(setting.get("date") or "")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return sent_date == today


def _mark_sent_today(conn: sqlite3.Connection) -> None:
    """Mark that the morning report has been sent today."""
    task_hub.ensure_schema(conn)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    task_hub._set_setting(conn, _LAST_REPORT_SENT_KEY, {
        "date": today,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    })


def is_report_due(
    conn: sqlite3.Connection,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Check if the morning report should be sent now.

    Returns True if:
      - Morning report is enabled
      - Current hour matches the report hour (default: 7 AM)
      - Report has not already been sent today
    """
    if not _is_morning_report_enabled():
        return False

    if now is None:
        tz_name = os.getenv("USER_TIMEZONE", "America/Chicago")
        try:
            import pytz
            tz = pytz.timezone(tz_name)
            now = datetime.now(tz)
        except Exception:
            now = datetime.now()

    report_hour = _get_report_hour()
    if now.hour != report_hour:
        return False

    if _already_sent_today(conn):
        return False

    return True


# ---------------------------------------------------------------------------
# Report Builder
# ---------------------------------------------------------------------------

def _get_overnight_activity(conn: sqlite3.Connection) -> dict[str, Any]:
    """Summarize tasks worked on overnight (since 10 PM yesterday)."""
    task_hub.ensure_schema(conn)
    # Look for tasks updated overnight (last 12 hours)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    rows = conn.execute(
        """
        SELECT task_id, title, status, project_key,
               created_at, updated_at
        FROM task_hub_items
        WHERE updated_at > ?
        ORDER BY updated_at DESC
        LIMIT 20
        """,
        (cutoff,),
    ).fetchall()
    recently_updated = [dict(r) for r in rows]

    # Categorize
    completed_overnight = [t for t in recently_updated if t.get("status") == "completed"]
    created_overnight = [
        t for t in recently_updated
        if t.get("created_at") and t["created_at"] > cutoff
        and t.get("status") != "completed"
    ]
    in_progress = [t for t in recently_updated if t.get("status") == "in_progress"]

    return {
        "total_overnight_updates": len(recently_updated),
        "completed_overnight": completed_overnight,
        "created_overnight": created_overnight,
        "in_progress": in_progress,
    }


def build_morning_email_body(conn: sqlite3.Connection) -> str:
    """Build the full morning report email body as formatted text."""
    # 1. Get the standard morning report from proactive_advisor
    advisor_report = build_morning_report(conn)

    # 2. Get overnight activity
    overnight = _get_overnight_activity(conn)

    # 3. Get reflection engine stats
    nightly_count = _get_nightly_task_count(conn)
    max_nightly = _parse_int_env("UA_REFLECTION_MAX_NIGHTLY_TASKS", DEFAULT_MAX_NIGHTLY_TASKS)
    open_count = _get_open_task_count(conn)
    stalled = _get_stalled_brainstorms(conn)

    # Build the email body
    lines: list[str] = []
    lines.append("Good morning, Kevin! ☀️")
    lines.append("")
    lines.append("Here's your daily task pipeline report.")
    lines.append("")

    # --- Overnight Autonomous Activity ---
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🌙 OVERNIGHT AUTONOMOUS ACTIVITY")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append(f"  Reflection cycles used: {nightly_count}/{max_nightly}")
    lines.append(f"  Tasks updated overnight: {overnight['total_overnight_updates']}")
    lines.append(f"  Tasks completed overnight: {len(overnight['completed_overnight'])}")
    lines.append(f"  New tasks created overnight: {len(overnight['created_overnight'])}")
    lines.append("")

    if overnight["completed_overnight"]:
        lines.append("  ✅ Completed overnight:")
        for t in overnight["completed_overnight"][:5]:
            lines.append(f"    • {t.get('title', 'Untitled')}")
        lines.append("")

    if overnight["created_overnight"]:
        lines.append("  🆕 Created overnight:")
        for t in overnight["created_overnight"][:5]:
            lines.append(f"    • {t.get('title', 'Untitled')}")
        lines.append("")

    # --- Task Hub State ---
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("📋 TASK HUB STATE")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append(f"  Active tasks: {advisor_report.get('total_active', 0)}")
    lines.append(f"  Open (queued): {open_count}")
    lines.append(f"  In progress: {len(overnight.get('in_progress', []))}")
    lines.append(f"  Unanswered questions: {advisor_report.get('unanswered_questions_count', 0)}")
    lines.append(f"  Expiring soon: {advisor_report.get('expiring_questions_count', 0)}")
    lines.append("")

    # --- Brainstorm Pipeline ---
    brainstorms = advisor_report.get("brainstorm_tasks") or []
    if brainstorms:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💡 BRAINSTORM PIPELINE")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        for b in brainstorms:
            stale_flag = " ⚠️ STALE" if b.get("is_stale") else ""
            lines.append(
                f"  • {b['title']} [{b['stage']}]{stale_flag}"
            )
            if b.get("pending_questions"):
                lines.append(f"    ↳ {b['pending_questions']} pending question(s)")
        lines.append("")

    # --- Stalled items ---
    if stalled:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("⚠️ ATTENTION NEEDED")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        for s in stalled:
            lines.append(
                f"  • {s.get('title', 'Untitled')} (stalled at '{s.get('refinement_stage', '?')}')"
            )
        lines.append("")

    stale_ip = advisor_report.get("stale_in_progress") or []
    if stale_ip:
        if not stalled:
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("⚠️ ATTENTION NEEDED")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("")
        lines.append("  Stale in-progress tasks:")
        for s in stale_ip:
            lines.append(f"    • {s.get('title', 'Untitled')} (stale {s.get('stale_hours', '?')}h)")
        lines.append("")

    # --- Footer ---
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("📊 View the full dashboard:")
    lines.append("  → /dashboard/todolist")
    lines.append("")
    lines.append("— Simone 🤖")

    return "\n".join(lines)


def build_morning_email_subject() -> str:
    """Build the email subject line with today's date."""
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    return f"☀️ Morning Report — {today}"


# ---------------------------------------------------------------------------
# Sender
# ---------------------------------------------------------------------------

class MorningReportSender:
    """Sends the daily morning report email via AgentMail.

    Designed to be called from:
    1. The gateway cron service (scheduled at 7 AM)
    2. The heartbeat service (first morning tick triggers check)
    """

    def __init__(
        self,
        *,
        agentmail_service: Any,
        task_hub_db_path: str = "",
    ) -> None:
        self._agentmail = agentmail_service
        self._db_path = task_hub_db_path

    def _get_conn(self) -> sqlite3.Connection:
        """Get a connection to the Task Hub database."""
        if self._db_path:
            conn = sqlite3.connect(self._db_path)
        else:
            conn = task_hub.get_connection()
        conn.row_factory = sqlite3.Row
        return conn

    async def send_if_due(
        self,
        *,
        conn: Optional[sqlite3.Connection] = None,
        now: Optional[datetime] = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Check if the morning report is due and send if so.

        Args:
            conn: Optional pre-existing DB connection.
            now: Optional current time (for testing).
            force: If True, skip the time/already-sent checks.

        Returns:
            Dict with status and details of what happened.
        """
        own_conn = conn is None
        if own_conn:
            conn = self._get_conn()

        try:
            if not force and not is_report_due(conn, now=now):
                return {"status": "not_due", "sent": False}

            recipient = _get_recipient_email()
            if not recipient:
                logger.warning("☀️ Morning report skipped: no recipient email configured")
                return {"status": "no_recipient", "sent": False}

            if self._agentmail is None:
                logger.warning("☀️ Morning report skipped: AgentMail service not available")
                return {"status": "no_agentmail", "sent": False}

            # Build the report
            subject = build_morning_email_subject()
            body = build_morning_email_body(conn)

            # Send via AgentMail
            try:
                result = await self._agentmail.send_email(
                    to=recipient,
                    subject=subject,
                    text=body,
                    labels=["morning-report", "system-generated"],
                    force_send=True,
                )
                _mark_sent_today(conn)
                logger.info(
                    "☀️ Morning report sent to=%s subject=%r message_id=%s",
                    recipient, subject, result.get("message_id"),
                )
                return {
                    "status": "sent",
                    "sent": True,
                    "recipient": recipient,
                    "subject": subject,
                    "message_id": result.get("message_id"),
                }
            except Exception as exc:
                logger.error("☀️ Morning report send failed: %s", exc, exc_info=True)
                return {"status": "send_failed", "sent": False, "error": str(exc)}
        finally:
            if own_conn and conn:
                conn.close()

    async def send_forced(
        self,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> dict[str, Any]:
        """Force-send the morning report regardless of time/sent state."""
        return await self.send_if_due(conn=conn, force=True)

