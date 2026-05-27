"""Invariant: operator_daily VP missions deliver within 2h of being queued.

Closes the monitoring gap that hid the 2026-05-27 morning briefing miss.
That morning the briefing cron dispatched a mission to Atlas's queue,
but Atlas was down + the briefing had default priority=100 (lowest
under ORDER BY priority ASC), so it sat queued for 4+ hours without
ever being claimed. No invariant fired because the existing
`morning_briefing_freshness` invariant looks for the on-disk
DAILY_BRIEFING.md artifact — a downstream signal that doesn't trigger
until well after the queue layer has already failed.

This invariant checks the queue layer directly: if any mission with
priority_tier='operator_daily' has been queued for >2h without being
claimed, that's an SLA breach the operator needs to see immediately —
regardless of whether the downstream artifact-writing step has even
been reached.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import sqlite3
from typing import Any, Dict, Optional

from universal_agent.services.pipeline_invariants import invariant

# Default 2h SLA — beyond this, operator notices the missing briefing.
# Operator-tunable via env without redeploying.
_SLA_HOURS = int(os.getenv("UA_OPERATOR_DAILY_MISSION_SLA_HOURS", "2"))


@invariant(
    id="operator_daily_mission_freshness",
    title="Operator-daily VP missions claimed within 2h of dispatch",
    description=(
        "Any vp_mission with priority_tier='operator_daily' (briefings, "
        "evening recap, YouTube digest) must be claimed within "
        f"{_SLA_HOURS}h of being queued. Beyond that, the operator's "
        "daily deliverable is late and they will notice. The probe "
        "looks at vp_missions directly — not the downstream artifact — "
        "so it catches queue starvation immediately, not after the "
        "missing-output cascade. Closes the 2026-05-27 morning briefing "
        "monitoring gap where a priority-sort bug starved the briefing "
        "queue for 4+ hours undetected."
    ),
    severity="critical",
    runbook_command=(
        "sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/vp_state.db "
        '"SELECT mission_id, mission_type, vp_id, priority_tier, '
        "datetime(created_at,'-5 hours') AS hou, status "
        "FROM vp_missions "
        "WHERE priority_tier='operator_daily' AND status='queued' "
        'ORDER BY created_at ASC;"'
    ),
    metadata={
        "pipeline": "vp_mission_queue",
        "tier_watched": "operator_daily",
        "sla_hours": _SLA_HOURS,
    },
)
def operator_daily_mission_freshness(
    ctx: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    activity_conn = ctx.get("activity_conn")
    # The probe queries vp_state.db (where vp_missions lives), not
    # activity_state.db. The proactive_health aggregator passes only
    # activity_conn in ctx, so we open a short-lived second connection
    # to the VP state DB. Best-effort: if either DB is unreadable we
    # quietly skip — invariants must never crash the heartbeat.
    if activity_conn is None:
        return None
    try:
        from universal_agent.durable.db import connect_runtime_db, get_vp_db_path
        vp_conn = connect_runtime_db(get_vp_db_path())
    except Exception:
        return None
    try:
        vp_conn.row_factory = sqlite3.Row
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=_SLA_HOURS)).isoformat()
        try:
            stale_rows = vp_conn.execute(
                """
                SELECT mission_id, mission_type, vp_id, created_at
                FROM vp_missions
                WHERE priority_tier = 'operator_daily'
                  AND status = 'queued'
                  AND cancel_requested = 0
                  AND created_at < ?
                ORDER BY created_at ASC
                LIMIT 10
                """,
                (cutoff,),
            ).fetchall()
        except sqlite3.OperationalError:
            # Schema doesn't have priority_tier yet (pre-deploy state).
            return None
    finally:
        try:
            vp_conn.close()
        except Exception:
            pass

    if not stale_rows:
        return None

    examples = [
        {
            "mission_id": str(r["mission_id"]),
            "mission_type": str(r["mission_type"] or ""),
            "vp_id": str(r["vp_id"]),
            "queued_at": str(r["created_at"]),
        }
        for r in stale_rows
    ]
    oldest = examples[0]
    return {
        "observed_value": {
            "stale_count": len(stale_rows),
            "examples": examples,
        },
        "threshold_text": (
            f"all operator_daily missions claimed within {_SLA_HOURS}h of dispatch"
        ),
        "message": (
            f"{len(stale_rows)} operator-daily VP mission(s) queued >"
            f"{_SLA_HOURS}h without being claimed. Oldest: "
            f"{oldest['mission_type'] or 'briefing'} on {oldest['vp_id']} "
            f"queued at {oldest['queued_at']}. The operator's daily "
            "deliverable is late — check VP worker liveness and queue "
            "depth."
        ),
    }
