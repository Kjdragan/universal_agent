"""Cron consecutive-failure invariant.

Sibling to ``cron_staleness``, which flags "this cron didn't fire on time"
and "the last outcome wasn't success". This one specifically catches the
silent-streak failure mode: a cron that IS firing on schedule but has
failed N runs in a row without anyone noticing.

The paper_to_podcast_daily incident (2026-05-23) motivated this:
``cron:paper_to_podcast_daily`` fired correctly six nights running and
died at ~5 min wall-clock every time. ``task_hub_items.updated_at`` kept
refreshing so the dashboard's NOT_ASSIGNED column looked healthy, and
``last_outcome`` on the cron object wasn't propagating cleanly so
``cron_staleness`` didn't fire either. The streak was only visible in
``task_hub_assignments``.

This invariant reads that table directly, scopes to ``task_id LIKE
'cron:%'``, and emits one finding listing every cron whose most recent
consecutive failure count crosses the threshold.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, List, Optional

from universal_agent.services.pipeline_invariants import invariant

logger = logging.getLogger(__name__)


# Number of consecutive non-success assignments at the head of the
# per-task assignment history that triggers the finding. 3 catches the
# pattern early enough that the next morning's operator sweep can act,
# while still tolerating one transient failure.
STREAK_THRESHOLD = 3

# How many recent assignments to inspect per cron task. The streak count
# can never exceed this value; covering 7 nights of nightly crons is
# enough headroom for the common case while keeping the query bounded.
WINDOW_PER_TASK = 7

# Assignment ``state`` values treated as success. Anything else
# (``failed``, ``cancelled``, ``signaled``, ``timeout_killed``,
# ``orphan_reconciled``, …) counts toward the failure streak.
SUCCESS_STATES = {"completed", "ok"}


def _streak_for_task(
    conn: sqlite3.Connection, task_id: str
) -> Dict[str, Any]:
    """Return {streak, last_state, last_started_at} for the given task.

    Walks the most recent ``WINDOW_PER_TASK`` assignments newest-first
    and counts the leading non-success run.
    """
    cur = conn.execute(
        "SELECT state, started_at FROM task_hub_assignments "
        "WHERE task_id = ? ORDER BY started_at DESC LIMIT ?",
        (task_id, WINDOW_PER_TASK),
    )
    rows = cur.fetchall()
    if not rows:
        return {"streak": 0, "last_state": None, "last_started_at": None}
    streak = 0
    last_state = str(rows[0][0] or "").strip().lower()
    last_started_at = str(rows[0][1] or "")
    for state, _ in rows:
        if str(state or "").strip().lower() in SUCCESS_STATES:
            break
        streak += 1
    return {
        "streak": streak,
        "last_state": last_state,
        "last_started_at": last_started_at,
    }


@invariant(
    id="cron_consecutive_failures",
    title="No cron has accumulated a multi-run failure streak",
    description=(
        "Walks ``task_hub_assignments`` for every distinct ``cron:*`` "
        "task_id, counts the most-recent consecutive non-success "
        "assignments, and flags any cron whose streak crosses the "
        "threshold. Complements cron_staleness, which catches single-run "
        "outcome errors; this one catches the silent-streak case where "
        "the cron is firing on schedule but every run is failing in the "
        "same way."
    ),
    severity="warn",
    runbook_command=(
        "sqlite3 -header -column "
        "/opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db "
        '"SELECT task_id, state, started_at, ended_at, result_summary '
        "FROM task_hub_assignments WHERE task_id LIKE 'cron:%' "
        'ORDER BY started_at DESC LIMIT 25;"'
    ),
    metadata={
        "context_key": "activity_conn",
        "design_note": (
            "Motivated by the 2026-05-23 paper_to_podcast incident: a "
            "cron failed 6 nights in a row at the same wall-clock cap "
            "and never surfaced because last_outcome propagation was "
            "unreliable. Reading task_hub_assignments directly avoids "
            "that dependency."
        ),
        "streak_threshold": STREAK_THRESHOLD,
        "window_per_task": WINDOW_PER_TASK,
    },
)
def cron_consecutive_failures(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Flag any ``cron:*`` task whose recent runs are a failing streak.

    Reads ``task_hub_assignments`` directly (not propagated ``last_outcome``),
    counts the most-recent consecutive non-success assignments per distinct
    cron task_id, and emits a finding for every cron whose streak crosses
    ``STREAK_THRESHOLD``. Catches the on-schedule-but-always-failing case that
    single-run staleness checks miss. Returns None when no streak qualifies.
    """
    conn = ctx.get("activity_conn")
    if conn is None:
        return None

    try:
        task_rows = conn.execute(
            "SELECT DISTINCT task_id FROM task_hub_assignments "
            "WHERE task_id LIKE 'cron:%'"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        logger.debug("cron_consecutive_failures: distinct query failed: %s", exc)
        return None

    streaks: List[Dict[str, Any]] = []
    for row in task_rows:
        task_id = str(row[0] or "").strip()
        if not task_id:
            continue
        try:
            data = _streak_for_task(conn, task_id)
        except sqlite3.DatabaseError as exc:
            logger.debug(
                "cron_consecutive_failures: per-task query failed for %s: %s",
                task_id,
                exc,
            )
            continue
        if data["streak"] >= STREAK_THRESHOLD:
            streaks.append({"task_id": task_id, **data})

    if not streaks:
        return None

    streaks.sort(key=lambda s: s["streak"], reverse=True)
    job_ids = sorted(s["task_id"].removeprefix("cron:") for s in streaks)
    return {
        "observed_value": {
            "streaks": streaks,
            "streak_count": len(streaks),
        },
        "threshold_text": (
            f"every cron's most-recent assignment streak < {STREAK_THRESHOLD} "
            "consecutive non-success runs"
        ),
        "message": (
            f"{len(streaks)} cron job(s) on a "
            f"≥{STREAK_THRESHOLD}-run failure streak: {', '.join(job_ids)}. "
            "Inspect task_hub_assignments and the most recent run workspace "
            "to determine whether the cron is wedged, hitting a wall-clock "
            "cap, or needs a generous timeout_seconds bump."
        ),
    }
