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
import sqlite3

logger = logging.getLogger(__name__)


async def _run_report() -> dict:
    """Compose and deliver the proactive intelligence report."""
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.infisical_loader import initialize_runtime_secrets
    from universal_agent.services.agentmail_service import AgentMailService
    from universal_agent.services.proactive_intelligence_report import (
        deliver_intelligence_report,
    )

    # One-shot cron `!script` subprocess: make sure the Infisical-backed secrets
    # (AgentMail API key, etc.) are present before we stand up the mailer.
    # Mirrors briefings_agent.py / insight_scoring_health.py.
    initialize_runtime_secrets(profile="local_workstation")

    # Resolve the DB path. The ``proactive_intelligence_reports`` rows are read
    # back by the dashboard from the canonical **activity_state.db** — NOT the
    # orphan ``workspaces/runtime_state.db`` this agent previously defaulted to.
    # Writing reports there made every delivered report invisible to the reader
    # (88 rows piled up in the orphan DB). Mirror proactive_digest_agent.py.
    #
    # Resolution order (highest precedence first):
    #   1. UA_DB_PATH env override (operator escape hatch)
    #   2. canonical get_activity_db_path()
    db_path = os.getenv("UA_DB_PATH", "")
    if not db_path:
        db_path = get_activity_db_path()

    recipient = os.getenv("UA_BRIEFING_RECIPIENT", "kevinjdragan@gmail.com")

    # Use the same connection helper the producers use so WAL + busy-timeout
    # pragmas match across writer + reader.
    conn = connect_runtime_db(db_path)
    conn.row_factory = sqlite3.Row

    # Real mailer — AgentMail primary, with the built-in gws/Gmail HTTP-429
    # fallback. There is intentionally NO dummy fallback: a no-op mailer that
    # returned ``{"status": "skipped", "message_id": ""}`` let
    # deliver_intelligence_report record ``email_sent=True`` for zero sent mail.
    # We construct the one true mailer; if it can't start we let delivery
    # degrade gracefully (email_sent=False) while still composing + storing the
    # report for the dashboard.
    mail_service = AgentMailService()
    await mail_service.startup()
    if not getattr(mail_service, "_started", False):
        logger.error(
            "AgentMail did not start (%s) — report will be composed and stored "
            "but NOT emailed.",
            getattr(mail_service, "_last_error", "unknown"),
        )

    try:
        result = await deliver_intelligence_report(
            conn=conn,
            mail_service=mail_service,
            recipient=recipient,
        )
        logger.info(
            "Proactive intelligence report delivered: report_id=%s period=%s "
            "email_sent=%s message_id=%s",
            result.get("report_id"),
            result.get("period"),
            result.get("email_sent"),
            result.get("email_message_id", ""),
        )
        return result
    except Exception as exc:
        logger.error("Failed to deliver proactive intelligence report: %s", exc, exc_info=True)
        return {"error": str(exc)}
    finally:
        try:
            await mail_service.shutdown()
        except Exception:  # noqa: BLE001 — best-effort one-shot teardown
            pass
        conn.close()


def main():
    """Synchronous entry point for the cron system."""
    return asyncio.run(_run_report())


# When loaded as a script by CronService (!script), this runs:
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = main()
    print(f"Report result: {result}")
