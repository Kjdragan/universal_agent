"""Cron entrypoint — `!script universal_agent.scripts.reconcile_vp_mission_prs`.

Runs every 15 minutes during active hours. Scans recent vp_mission tasks
in non-terminal state, queries GitHub for the associated PR's merge
status, and auto-closes the task when the PR has merged.

See `services/vp_mission_pr_reconciler.py` for the design rationale and
the 2026-05-11 incident (`vp-mission-95e1a15a3b0ec8dbf58db662`) that
motivated this loop.

Supports `--dry-run` for one-shot operator audits via:
  PYTHONPATH=src uv run python -m universal_agent.scripts.reconcile_vp_mission_prs --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys

from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.services.vp_mission_pr_reconciler import (
    reconcile_vp_missions_with_prs,
)

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile VP missions with shipped PRs.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report candidates without writing.",
    )
    args = parser.parse_args()

    try:
        conn: sqlite3.Connection = connect_runtime_db(get_activity_db_path())
    except Exception:
        logger.exception("reconcile_vp_mission_prs: could not open runtime DB")
        return 1

    try:
        result = reconcile_vp_missions_with_prs(conn, dry_run=args.dry_run)
    except Exception:
        logger.exception("reconcile_vp_mission_prs: reconciliation crashed")
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass

    logger.info(
        "reconcile_vp_mission_prs: scanned=%d closed=%d still_open=%d "
        "pr_deleted=%d errors=%d skipped_no_token=%d cloudflare_skipped=%d dry_run=%s",
        result["scanned"],
        result["closed"],
        result["still_open"],
        result["pr_deleted"],
        result["errors"],
        result["skipped_no_token"],
        result.get("cloudflare_skipped", 0),
        args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
