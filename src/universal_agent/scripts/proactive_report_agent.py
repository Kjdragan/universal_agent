"""
proactive_report_agent.py — Cron-triggered entry point for 3x daily intelligence reports.

Invoked by CronService at 7 AM, 12 PM, and 4 PM CT.
Composes a hybrid report (deterministic stats + LLM reasoning) and delivers
it via email and stores it for dashboard retrieval.

Usage in cron_jobs.json:
  "command": "!script universal_agent.scripts.proactive_report_agent"
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import sqlite3

logger = logging.getLogger(__name__)


async def _run_report() -> dict:
    """Compose and deliver the proactive intelligence report."""
    from universal_agent.services.proactive_intelligence_report import (
        deliver_intelligence_report,
    )

    # Resolve the DB path (same pattern as briefings_agent.py)
    db_path = os.getenv("UA_DB_PATH", "")
    if not db_path:
        workspace_root = os.getenv("UA_WORKSPACE_ROOT", "/opt/universal_agent")
        db_path = str(Path(workspace_root) / "workspaces" / "runtime_state.db")

    recipient = os.getenv("UA_BRIEFING_RECIPIENT", "kevinjdragan@gmail.com")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Import and create mail service (same pattern as briefings_agent)
    mail_service = None
    try:
        from universal_agent.services.mail_service import MailService
        mail_service = MailService()
    except Exception as exc:
        logger.warning("MailService not available: %s", exc)
        # Create a dummy that logs instead of sending
        class _DummyMail:
            async def send_email(self, **kwargs):
                logger.info("REPORT (no mailer): subject=%s", kwargs.get("subject", ""))
                return {"status": "skipped", "message_id": "", "thread_id": ""}
        mail_service = _DummyMail()

    try:
        result = await deliver_intelligence_report(
            conn=conn,
            mail_service=mail_service,
            recipient=recipient,
        )
        logger.info(
            "Proactive intelligence report delivered: report_id=%s period=%s email_sent=%s",
            result.get("report_id"),
            result.get("period"),
            result.get("email_sent"),
        )
        return result
    except Exception as exc:
        logger.error("Failed to deliver proactive intelligence report: %s", exc, exc_info=True)
        return {"error": str(exc)}
    finally:
        conn.close()


def main():
    """Synchronous entry point for the cron system."""
    return asyncio.run(_run_report())


# When loaded as a script by CronService (!script), this runs:
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = main()
    print(f"Report result: {result}")
