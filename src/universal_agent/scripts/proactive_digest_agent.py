"""
proactive_digest_agent.py — Cron-triggered daily proactive artifact digest.

Automatically surfaces unseen proactive artifacts (CODIE PRs, tutorial builds,
convergence insights, etc.) by composing and sending the daily digest email.

This bridges the gap where artifacts accumulate with status='candidate' and
delivery_state='not_surfaced' because nothing was triggering the digest email.

Usage in cron_jobs.json:
  "command": "!script universal_agent.scripts.proactive_digest_agent"
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import sqlite3

logger = logging.getLogger(__name__)


async def _run_digest() -> dict:
    """Compose and send the daily proactive artifact digest email."""
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.services.intelligence_reporter import IntelligenceReporter

    # Resolve the DB path. `proactive_artifacts` (insight_brief_task, codie PRs,
    # convergence briefs, etc.) lives in the **activity_state.db**, not the
    # runtime_state.db that the script previously defaulted to. The original
    # ``workspaces/runtime_state.db`` fallback meant the digest was reading
    # an empty/wrong DB for ~weeks — see audit 2026-05-22 (638 briefs piled
    # up in activity_state.db with zero email deliveries).
    #
    # Resolution order (highest precedence first):
    #   1. UA_DB_PATH env override (operator escape hatch)
    #   2. canonical get_activity_db_path() — same path the convergence cron
    #      writes to via connect_runtime_db(get_activity_db_path()).
    db_path = os.getenv("UA_DB_PATH", "")
    if not db_path:
        db_path = get_activity_db_path()

    recipient = os.getenv("UA_BRIEFING_RECIPIENT", "kevinjdragan@gmail.com")

    # Use the same connection helper the producers use so WAL +
    # busy-timeout pragmas match across writer + reader.
    conn = connect_runtime_db(db_path)
    conn.row_factory = sqlite3.Row

    # Initialize mail service
    mail_service = None
    try:
        from universal_agent.services.mail_service import MailService
        mail_service = MailService()
    except Exception as exc:
        logger.warning("MailService not available: %s", exc)

        class _DummyMail:
            async def send_email(self, **kwargs):
                logger.info("DIGEST (no mailer): subject=%s", kwargs.get("subject", ""))
                return {"status": "skipped", "message_id": "", "thread_id": ""}

        mail_service = _DummyMail()

    try:
        result = await IntelligenceReporter(conn).send_daily_digest(
            recipient=recipient,
            mail_service=mail_service,
            limit=20,
        )
        logger.info(
            "Proactive artifact digest sent: artifact_id=%s to=%s",
            result.get("artifact_id"),
            result.get("to"),
        )
        return result
    except Exception as exc:
        logger.error("Failed to send proactive artifact digest: %s", exc, exc_info=True)
        return {"error": str(exc)}
    finally:
        conn.close()


def main():
    """Synchronous entry point for the cron system."""
    return asyncio.run(_run_digest())


# When loaded as a script by CronService (!script), this runs:
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = main()
    print(f"Digest result: {result}")
