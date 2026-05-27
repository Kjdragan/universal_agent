"""One-shot operator script: cancel all queued VP missions.

Used to flush the post-priority-tiers-PR backlog so we start fresh and
can observe whether the system stays in equilibrium with the new
ordering. Doesn't touch missions currently `running` — those have
workers actively executing them. Doesn't touch terminal states
(`completed`, `failed`, `cancelled`).

Run with `--dry-run` first to preview what would be cancelled. The
script writes an audit snapshot (mission_id, mission_type, priority_tier,
created_at) to ``AGENT_RUN_WORKSPACES/flush_audit/<timestamp>.json``
before mutating anything — so the cancellation is reversible by hand
if the operator changes their mind (though re-queueing is mostly
pointless since proactive pipelines will regenerate the work).

Idempotent: running twice is harmless (the second run finds nothing
to cancel).

Usage:
    uv run python -m universal_agent.scripts.flush_vp_mission_backlog --dry-run
    uv run python -m universal_agent.scripts.flush_vp_mission_backlog
    uv run python -m universal_agent.scripts.flush_vp_mission_backlog \\
        --vp vp.general.primary --reason "post-priority-tiers PR clean slate"
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys

from universal_agent.infisical_loader import initialize_runtime_secrets

logger = logging.getLogger(__name__)


def _open_vp_conn() -> sqlite3.Connection:
    from universal_agent.durable.db import connect_runtime_db, get_vp_db_path
    conn = connect_runtime_db(get_vp_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _audit_dir() -> Path:
    base = os.getenv("AGENT_RUN_WORKSPACES") or "/opt/universal_agent/AGENT_RUN_WORKSPACES"
    return Path(base) / "flush_audit"


def _write_audit(rows: list[dict], reason: str) -> Path:
    audit_dir = _audit_dir()
    audit_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = audit_dir / f"vp_mission_backlog_flush_{ts}.json"
    payload = {
        "flushed_at_utc": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "count": len(rows),
        "missions": rows,
    }
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def _list_queued(
    conn: sqlite3.Connection,
    vp_id_filter: str | None,
) -> list[dict]:
    sql = """
        SELECT mission_id, vp_id, mission_type, priority_tier, priority,
               status, created_at
        FROM vp_missions
        WHERE status = 'queued'
          AND cancel_requested = 0
    """
    params: tuple = ()
    if vp_id_filter:
        sql += " AND vp_id = ?"
        params = (vp_id_filter,)
    sql += " ORDER BY created_at ASC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def _cancel(
    conn: sqlite3.Connection,
    mission_ids: list[str],
    reason: str,
) -> int:
    if not mission_ids:
        return 0
    now_iso = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" * len(mission_ids))
    cursor = conn.execute(
        f"""
        UPDATE vp_missions
        SET status = 'cancelled',
            cancel_requested = 1,
            updated_at = ?,
            completed_at = COALESCE(completed_at, ?)
        WHERE mission_id IN ({placeholders})
          AND status = 'queued'
        """,
        (now_iso, now_iso, *mission_ids),
    )
    affected = int(cursor.rowcount or 0)
    # Append an event row per mission so the lifecycle audit trail
    # records why the cancellation happened.
    import uuid as _uuid
    for mission_id in mission_ids:
        conn.execute(
            """
            INSERT INTO vp_events
              (event_id, mission_id, vp_id, event_type, payload_json, created_at)
            VALUES (?, ?, (SELECT vp_id FROM vp_missions WHERE mission_id = ?),
                    'vp.mission.cancelled', ?, ?)
            """,
            (
                f"vp-event-{_uuid.uuid4().hex}",
                mission_id,
                mission_id,
                json.dumps({"reason": reason, "source": "flush_vp_mission_backlog"}),
                now_iso,
            ),
        )
    conn.commit()
    return affected


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List what would be cancelled without mutating.",
    )
    parser.add_argument(
        "--vp", default=None,
        help="Restrict to a single vp_id (e.g. vp.general.primary). Default: all.",
    )
    parser.add_argument(
        "--reason", default="manual flush of queued backlog",
        help="Cancellation reason recorded in vp_events + audit JSON.",
    )
    args = parser.parse_args()

    # Load Infisical secrets so the durable.db helper can resolve the
    # vp_state.db path correctly under the same env contract the gateway uses.
    try:
        initialize_runtime_secrets()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Infisical bootstrap skipped: %s", exc)

    conn = _open_vp_conn()
    try:
        rows = _list_queued(conn, args.vp)
        if not rows:
            logger.info("No queued missions to flush%s.", f" for {args.vp}" if args.vp else "")
            return 0

        # Group by (vp_id, priority_tier) for the preview
        from collections import Counter
        breakdown: Counter[tuple] = Counter()
        for r in rows:
            breakdown[(str(r["vp_id"]), str(r["priority_tier"] or "background"))] += 1

        logger.info("Found %d queued mission(s):", len(rows))
        for (vp_id, tier), count in sorted(breakdown.items()):
            logger.info("  %s / %s: %d", vp_id, tier, count)

        if args.dry_run:
            logger.info("--dry-run set; no mutations performed.")
            return 0

        audit_path = _write_audit(rows, args.reason)
        logger.info("Audit snapshot: %s", audit_path)

        mission_ids = [str(r["mission_id"]) for r in rows]
        affected = _cancel(conn, mission_ids, args.reason)
        logger.info("Cancelled %d mission(s).", affected)
        return 0
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
