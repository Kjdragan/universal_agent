"""
morning_ideation_report.py — Cron entry point for the daily ideation report.

Composes the morning ideation report from the held reflection proposals Simone's
autonomous ideation generated overnight, publishes it to the tailnet scratchpad,
and emails the operator a link + the proposals with one-click promote/dismiss.

Usage in cron_jobs.json:
  "command": "!script universal_agent.scripts.morning_ideation_report"

Fixed-time cron (early morning) — fixed-time crons are exempt from dormancy.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)


async def _run() -> dict:
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.infisical_loader import initialize_runtime_secrets
    from universal_agent.services.agentmail_service import AgentMailService
    from universal_agent.services.ideation_report import deliver_ideation_report

    # One-shot subprocess: load Infisical-backed secrets (AgentMail key, the
    # UA_ARTIFACT_ACK_SECRET that signs the action links) before standing up the
    # mailer. No hardcoded profile so UA_DEPLOYMENT_PROFILE is honored.
    initialize_runtime_secrets()

    db_path = os.getenv("UA_DB_PATH", "") or get_activity_db_path()
    recipient = os.getenv("UA_BRIEFING_RECIPIENT", "kevinjdragan@gmail.com")

    conn = connect_runtime_db(db_path)
    conn.row_factory = sqlite3.Row

    mail_service = AgentMailService()
    await mail_service.startup()
    if not getattr(mail_service, "_started", False):
        logger.error(
            "AgentMail did not start (%s) — ideation report will publish to scratch "
            "but NOT email.",
            getattr(mail_service, "_last_error", "unknown"),
        )

    try:
        result = await deliver_ideation_report(
            conn=conn,
            mail_service=mail_service,
            recipient=recipient,
        )
        logger.info("Morning ideation report: %s", result)
        return result
    except Exception as exc:
        logger.error("Failed to deliver morning ideation report: %s", exc, exc_info=True)
        return {"error": str(exc)}
    finally:
        try:
            await mail_service.shutdown()
        except Exception:  # noqa: BLE001 — best-effort one-shot teardown
            pass
        conn.close()


def main():
    return asyncio.run(_run())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"Ideation report result: {main()}")
