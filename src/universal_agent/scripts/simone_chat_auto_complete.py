"""Cron entry point for `simone_chat_tasks.auto_complete_stale`.

Runs every minute (configurable via `UA_SIMONE_CHAT_AUTOCOMPLETE_CRON`)
and promotes `simone_chat` Task Hub rows whose completion proposal has
gone idle past `UA_SIMONE_CHAT_IDLE_MINUTES` (default 10).

Registered at gateway startup by `_ensure_simone_chat_autocomplete_cron`.
"""

from __future__ import annotations

import logging
import os
import sys


def main() -> int:
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.infisical_loader import initialize_runtime_secrets
    from universal_agent.services import simone_chat_tasks

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # The activity DB is local to the host — secrets aren't strictly required.
    # Initialize anyway so the script behaves identically inside the cron
    # worker subprocess as it would in-process.
    try:
        initialize_runtime_secrets(profile="local_workstation")
    except Exception:
        logger.warning("initialize_runtime_secrets failed; proceeding without it", exc_info=True)

    try:
        idle_minutes = int(os.getenv("UA_SIMONE_CHAT_IDLE_MINUTES", "10"))
    except ValueError:
        idle_minutes = 10

    conn = connect_runtime_db(get_activity_db_path())
    try:
        promoted = simone_chat_tasks.auto_complete_stale(
            conn, idle_threshold_minutes=idle_minutes
        )
    finally:
        conn.close()
    logger.info("simone_chat_auto_complete promoted=%d ids=%s", len(promoted), promoted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
