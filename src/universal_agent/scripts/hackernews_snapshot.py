"""Cron entrypoint — invoked via `!script universal_agent.scripts.hackernews_snapshot`."""
from __future__ import annotations

import logging
import sys

from universal_agent.services.hackernews_snapshot_service import (
    build_snapshot,
    write_snapshot,
)

logger = logging.getLogger(__name__)


def main() -> int:
    try:
        snapshot = build_snapshot()
    except RuntimeError as exc:
        logger.error("hackernews snapshot aborted: %s", exc)
        return 1
    except Exception:
        logger.exception("hackernews snapshot crashed unexpectedly")
        return 1

    try:
        path = write_snapshot(snapshot)
    except Exception:
        logger.exception("hackernews snapshot write failed")
        return 1

    errs = snapshot["meta"].get("errors", [])
    duration = snapshot["meta"].get("duration_seconds") or 0.0
    logger.info(
        "hackernews snapshot written path=%s errors=%d duration=%.1fs",
        path,
        len(errs),
        duration,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
