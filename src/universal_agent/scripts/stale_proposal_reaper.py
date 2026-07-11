"""stale_proposal_reaper.py — Cron entry point for the weekly stale-proposal reaper.

Parks OPEN reflection/brainstorm Task Hub proposals older than 14 days (default)
through ``task_hub.reap_stale_proposals`` — parked, never hard-deleted, with a
``metadata.stale_proposal_reap`` marker + evaluation row so the audit trail
survives. HARD GATE: priority>=2 and ``human-only`` items are always spared.

Emits a pruned-proposals digest to ``work_products/stale_proposal_reaper/``
under ``artifacts.resolve_artifacts_dir()`` so nothing vanishes silently.

Usage in cron_jobs.json:
  "command": "!script universal_agent.scripts.stale_proposal_reaper"

Weekly fixed-time cron (Sunday 06:40 CT) — fixed-time crons are exempt from
dormancy (project_docs/08_operations/03_dormancy_and_operating_hours.md).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import sqlite3

from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.infisical_loader import initialize_runtime_secrets

logger = logging.getLogger(__name__)

DEFAULT_REAP_DAYS = 14


def _write_digest(summary: dict, *, older_than_days: int) -> str | None:
    """Write the pruned-proposals digest under the artifacts work_products dir.

    Path resolved via ``artifacts.resolve_artifacts_dir`` — never guessed. The
    digest is best-effort: a failure here must NOT fail the reaper (the parks
    already committed); it only means no digest file for this run.
    """
    try:
        root = resolve_artifacts_dir()
        out_dir = root / "work_products" / "stale_proposal_reaper"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = out_dir / f"digest-{stamp}.json"
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "older_than_days": older_than_days,
            "summary": summary,
        }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        logger.info("stale_proposal_reaper: digest written to %s", path)
        return str(path)
    except Exception as exc:  # noqa: BLE001 — best-effort digest
        logger.error("stale_proposal_reaper: failed to write digest: %s", exc, exc_info=True)
        return None


async def _run() -> dict:
    # One-shot subprocess: load Infisical-backed secrets before touching the DB.
    initialize_runtime_secrets()

    db_path = os.getenv("UA_DB_PATH", "") or get_activity_db_path()
    older_than_days = int(os.getenv("UA_STALE_PROPOSAL_REAP_DAYS", str(DEFAULT_REAP_DAYS)))

    conn = connect_runtime_db(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from universal_agent import task_hub

        summary = task_hub.reap_stale_proposals(
            conn, older_than_days=older_than_days, via="weekly_cron",
        )
        digest_path = _write_digest(summary, older_than_days=older_than_days)
        summary["digest_path"] = digest_path
        logger.info("stale_proposal_reaper: %s", summary)
        return summary
    except Exception as exc:
        logger.error("stale_proposal_reaper failed: %s", exc, exc_info=True)
        return {"error": str(exc)}
    finally:
        conn.close()


def main():
    return asyncio.run(_run())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"stale_proposal_reaper result: {main()}")
