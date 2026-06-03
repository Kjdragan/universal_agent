#!/usr/bin/env python3
"""One-time migration: close legacy parked/needs_review proactive_health rows.

Background
----------
``proactive_health`` Task Hub rows were historically created as watchdog
backlog (status ``needs_review`` / ``parked``) every time a Layer-1/Layer-2
invariant fired. That surface has moved into Mission Control's *System Health*
panel, served live by ``GET /api/v1/ops/proactive_health`` (see
``services/proactive_health.build_proactive_health_payload``). The old Task Hub
rows are now redundant noise — they never auto-clear and they inflate the
parked/needs_review counts the dashboard and Simone's digest read.

This migration closes those legacy rows via the canonical close path,
``task_hub.perform_task_action(..., action='complete', ...)``. For a
``source_kind='proactive_health'`` row with no ``workflow_manifest.final_channel``,
no ``delivery_mode``, and a reason string that contains NO email-ish word,
``_task_requires_verified_final_delivery`` returns ``False`` — so ``complete``
sets ``status='completed'`` (plus a ``completion_token`` that also blocks any
future re-emit) and does NOT re-park the row in ``needs_review``.

Idempotency
-----------
The status filter (``status NOT IN ('completed','failed','cancelled')``) skips
already-closed rows, so re-running is a safe no-op. ``--dry-run`` lists what it
would do without mutating anything.

Usage
-----
    uv run python scripts/migrate_proactive_health_parked_rows.py [--dry-run]
    uv run python scripts/migrate_proactive_health_parked_rows.py --db-path /tmp/activity.db
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, Optional, Set

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from universal_agent import task_hub
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.services.proactive_health import build_proactive_health_payload

SOURCE_KIND = "proactive_health"
AGENT_ID = "migration:proactive_health_surface_move"

# Statuses we treat as already-terminal — re-running must skip these so the
# migration is idempotent.
TERMINAL_STATUSES = ("completed", "failed", "cancelled")

REASON_RECOVERED = (
    "proactive_health invariant recovered; closing legacy watchdog row "
    "(surfaced live in Mission Control System Health panel)"
)
REASON_STILL_FIRING = (
    "proactive_health finding still active; now surfaced in Mission Control "
    "System Health panel via /api/v1/ops/proactive_health"
)


def _currently_firing_finding_ids(conn: sqlite3.Connection) -> Set[str]:
    """Compute the set of finding_ids currently reported by the live watchdog.

    finding_id looks like ``invariant:<id>`` — see
    ``services/pipeline_invariants`` and ``HeartbeatFinding.finding_id``.
    """
    payload = build_proactive_health_payload(activity_conn=conn)
    firing: Set[str] = set()
    for finding in payload.get("invariants") or []:
        fid = ""
        if isinstance(finding, dict):
            fid = str(finding.get("finding_id") or "").strip()
        if fid:
            firing.add(fid)
    return firing


def _finding_id_from_metadata(metadata_json: Optional[str]) -> str:
    """Extract ``metadata.finding_id`` from the row's stored metadata blob."""
    if not metadata_json:
        return ""
    try:
        meta = json.loads(metadata_json)
    except (TypeError, ValueError):
        return ""
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("finding_id") or "").strip()


def run_migration(
    *,
    db_path: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Close legacy parked/needs_review proactive_health rows.

    Returns a summary dict: ``{scanned, completed, recovered, still_firing, skipped}``.
    """
    resolved_path = db_path or get_activity_db_path()
    conn = connect_runtime_db(resolved_path)
    try:
        task_hub.ensure_schema(conn)

        currently_firing = _currently_firing_finding_ids(conn)

        placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
        rows = conn.execute(
            f"""
            SELECT task_id, status, metadata_json
            FROM task_hub_items
            WHERE source_kind = ?
              AND status NOT IN ({placeholders})
            ORDER BY updated_at ASC
            """,
            (SOURCE_KIND, *TERMINAL_STATUSES),
        ).fetchall()

        summary: Dict[str, Any] = {
            "scanned": len(rows),
            "completed": 0,
            "recovered": 0,
            "still_firing": 0,
            "skipped": 0,
            "actions": [],
        }

        for row in rows:
            task_id = row["task_id"]
            finding_id = _finding_id_from_metadata(row["metadata_json"])
            recovered = finding_id not in currently_firing
            reason = REASON_RECOVERED if recovered else REASON_STILL_FIRING

            if recovered:
                summary["recovered"] += 1
            else:
                summary["still_firing"] += 1

            action_record = {
                "task_id": task_id,
                "finding_id": finding_id,
                "prior_status": row["status"],
                "recovered": recovered,
                "reason": reason,
            }

            if dry_run:
                summary["actions"].append({**action_record, "applied": False})
                continue

            try:
                task_hub.perform_task_action(
                    conn,
                    task_id=task_id,
                    action="complete",
                    reason=reason,
                    agent_id=AGENT_ID,
                )
                summary["completed"] += 1
                summary["actions"].append({**action_record, "applied": True})
            except Exception as exc:  # noqa: BLE001 — one bad row shouldn't abort the sweep
                summary["skipped"] += 1
                summary["actions"].append(
                    {**action_record, "applied": False, "error": str(exc)}
                )

        return summary
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Close legacy parked/needs_review proactive_health Task Hub rows "
            "(the surface moved to Mission Control's System Health panel)."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be closed without mutating any rows.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Override the activity DB path (default: get_activity_db_path()).",
    )
    args = parser.parse_args(argv)

    summary = run_migration(db_path=args.db_path, dry_run=args.dry_run)
    # JSON summary to stdout — re-runnable; second run reports scanned=0.
    print(json.dumps({"dry_run": args.dry_run, **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
