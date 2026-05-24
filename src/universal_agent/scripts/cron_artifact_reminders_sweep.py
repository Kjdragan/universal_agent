"""Cron-driven entry point for the artifact reminder sweep.

Invoked by the gateway-registered ``cron_artifact_reminders_sweep``
system cron (default every 30 minutes during 6 AM – 10 PM Houston).
Calls ``cron_artifact_reminders.sweep_pending_artifact_reminders``
and exits 0 on success, 1 if no mail service is available.

Designed to be lightweight: no SDK turn, just direct DB + AgentMail.
"""

from __future__ import annotations

import asyncio
import logging
import sys

logger = logging.getLogger(__name__)


async def _async_main() -> int:
    # Lazy imports so a partial install doesn't crash before logging is configured.
    from universal_agent import gateway_server
    from universal_agent.services.cron_artifact_reminders import (
        sweep_pending_artifact_reminders,
    )

    mail_service = getattr(gateway_server, "_agentmail_service", None)
    if mail_service is None:
        logger.warning(
            "cron_artifact_reminders_sweep: AgentMail service not initialized; nothing to do"
        )
        return 1

    recipient = gateway_server._proactive_review_recipient("")
    import os as _os
    dashboard_base_url = (
        _os.getenv("FRONTEND_URL", "")
        or _os.getenv("UA_PUBLIC_BASE_URL", "")
        or "https://app.clearspringcg.com"
    )

    # Use the gateway's own activity connection helper to share the
    # same SQLite tuning (busy_timeout etc.) as every other writer.
    conn = gateway_server._activity_connect()
    try:
        report = await sweep_pending_artifact_reminders(
            conn=conn,
            mail_service=mail_service,
            recipient=recipient,
            dashboard_base_url=dashboard_base_url,
        )
    finally:
        conn.close()

    logger.info("cron_artifact_reminders_sweep report: %s", report)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return asyncio.run(_async_main())


if __name__ == "__main__":
    sys.exit(main())
