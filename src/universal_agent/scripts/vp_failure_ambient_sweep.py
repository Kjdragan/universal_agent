"""Cron entrypoint — ``!script universal_agent.scripts.vp_failure_ambient_sweep``.

Runs 3x/day during active hours. Ambient-closes stale, single (non-recurring)
``vp_mission_failure`` Task Hub items via
:func:`universal_agent.task_hub.close_ambient_vp_failures`. This is the
self-healing counterpart to the operator/Simone-driven ``bulk_close_ambient``
Task Hub verb; together they prevent a resolved systemic event (e.g. the
2026-06-11 pile of 119 failure items) from re-accumulating and burying real
failure signals.

Guard (enforced in ``close_ambient_vp_failures``): never closes
``failure_count >= 2`` or items younger than the 48h guard window, and never
closes items whose ``created_at`` cannot be parsed. TTL window default 7 days.

Thresholds overridable via env:

* ``UA_VP_FAILURE_AMBIENT_TTL_DAYS`` (default 7)
* ``UA_VP_FAILURE_AMBIENT_GUARD_HOURS`` (default 48)
* ``UA_VP_FAILURE_AMBIENT_MAX_FAILURE_COUNT`` (default 1)

Supports ``--dry-run`` for one-shot operator audits:

    PYTHONPATH=src uv run python -m universal_agent.scripts.vp_failure_ambient_sweep --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys

from universal_agent import task_hub
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ambient-close stale single vp_mission_failure Task Hub items."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report candidates without writing.",
    )
    args = parser.parse_args()

    ttl_days = _env_int("UA_VP_FAILURE_AMBIENT_TTL_DAYS", task_hub.DEFAULT_AMBIENT_TTL_DAYS)
    guard_hours = _env_int(
        "UA_VP_FAILURE_AMBIENT_GUARD_HOURS", task_hub.DEFAULT_AMBIENT_GUARD_HOURS
    )
    max_fc = _env_int(
        "UA_VP_FAILURE_AMBIENT_MAX_FAILURE_COUNT",
        task_hub.DEFAULT_AMBIENT_MAX_FAILURE_COUNT,
    )

    conn: sqlite3.Connection = connect_runtime_db(get_activity_db_path())
    conn.row_factory = sqlite3.Row
    try:
        task_hub.ensure_schema(conn)
        summary = task_hub.close_ambient_vp_failures(
            conn,
            older_than_days=ttl_days,
            max_failure_count=max_fc,
            within_hours_guard=guard_hours,
            agent_id="ambient_sweep_cron",
            via="ttl",
            dry_run=args.dry_run,
        )
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass

    logger.info(
        "vp_failure_ambient_sweep via=%s dry_run=%s closed=%d skipped=%d reasons=%s",
        summary["via"],
        summary["dry_run"],
        summary["closed"],
        summary["skipped"],
        summary["skipped_reasons"],
    )
    print(
        f"[ambient-sweep] via={summary['via']} dry_run={summary['dry_run']} "
        f"closed={summary['closed']} skipped={summary['skipped']} "
        f"reasons={summary['skipped_reasons']} guard={summary['guard']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
