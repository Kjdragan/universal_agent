"""Cron entrypoint — invoked via `!script universal_agent.scripts.atlas_direct_dispatch`.

Hermes Phase C — runs every 60s when enabled. Dispatches Atlas-pre-tagged
tasks without waiting for Simone's heartbeat. See
`services/atlas_direct_dispatch.py` for the design rationale and
`docs/reports/hermes-adaptation-phased-plan-2026-05-10.md` § Phase C.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
from universal_agent.services.atlas_direct_dispatch import (
    _is_enabled,
    dispatch_atlas_candidates_once,
)

logger = logging.getLogger(__name__)


def main() -> int:
    if not _is_enabled():
        logger.info(
            "atlas_direct_dispatch disabled (UA_ATLAS_DIRECT_DISPATCH_ENABLED!=1); skipping"
        )
        return 0

    try:
        conn = connect_runtime_db(get_runtime_db_path())
    except Exception:
        logger.exception("atlas_direct_dispatch could not open runtime DB")
        return 1

    try:
        result = asyncio.run(dispatch_atlas_candidates_once(conn))
    except Exception:
        logger.exception("atlas_direct_dispatch sweep crashed")
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass

    logger.info(
        "atlas_direct_dispatch: dispatched=%d skipped=%d slots_at_start=%d reason=%s",
        result.get("dispatched", 0),
        result.get("skipped", 0),
        result.get("remaining_slots_at_start", 0),
        result.get("reason", ""),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
