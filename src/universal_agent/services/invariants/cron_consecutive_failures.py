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

from datetime import datetime, timezone
import logging
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

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

# Recency backstop. A "failure streak" is only meaningful while the cron
# is *actively appending runs*; once it stops (disabled, deleted, or its
# scheduler wedged) the leading-failure count freezes and would otherwise
# read the same value forever (the claude_code_intel_sync incident: a cron
# disabled 2026-05-25 kept its 6-failure streak on the board indefinitely).
# The primary guard is the enabled-and-known filter below (precise, zero
# false-negative risk). This flat cutoff is a defence-in-depth backstop for
# the degraded path where cron metadata is unavailable, so an ancient
# orphaned task can't freeze on the board. It is deliberately longer than
# the longest cron cadence in the system (monthly, ``0 7 1 * *`` ≈ 31 days)
# so it can NEVER suppress a legitimately-failing weekly/monthly cron.
RECENCY_CUTOFF_SECONDS = 35 * 24 * 3600  # 35 days


def _parse_started_at(value: Any) -> Optional[datetime]:
    """Best-effort parse of an assignment ``started_at`` into aware UTC."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, AttributeError):
        return None


def _enabled_cron_index(cron_jobs: Any) -> Tuple[bool, Set[str]]:
    """Derive ``(have_metadata, enabled_job_ids)`` from the ctx cron_jobs.

    ``have_metadata`` is True only when at least one usable cron row was
    found, so the caller can distinguish "this cron is disabled/deleted"
    from "we simply have no cron metadata this tick" and avoid going dark.
    """
    enabled: Set[str] = set()
    have_metadata = False
    for raw in cron_jobs or ():
        if isinstance(raw, dict):
            cj = raw
        elif hasattr(raw, "to_dict"):
            try:
                cj = raw.to_dict()
            except Exception:  # noqa: BLE001
                continue
        else:
            continue
        job_id = str(cj.get("job_id") or cj.get("id") or "").strip()
        if not job_id:
            continue
        have_metadata = True
        if bool(cj.get("enabled", True)):
            enabled.add(job_id)
    return have_metadata, enabled


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
            "that dependency. A streak is only counted for crons that are "
            "currently registered AND enabled (via ctx['cron_jobs']); a "
            "disabled or deleted cron stops appending runs, so its leading-"
            "failure count is frozen, not live (the 2026-05-25 "
            "claude_code_intel_sync false-card fix). A 35-day recency "
            "backstop guards the degraded path where cron metadata is "
            "unavailable."
        ),
        "streak_threshold": STREAK_THRESHOLD,
        "window_per_task": WINDOW_PER_TASK,
        "recency_cutoff_seconds": RECENCY_CUTOFF_SECONDS,
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

    # Authoritative cron metadata for this tick. Used to suppress streaks for
    # crons that can't possibly be "actively failing on schedule": disabled
    # crons and crons that have been deleted from the registry entirely. When
    # no metadata is available (a startup race or a caller that didn't supply
    # cron_jobs) we fall back to the recency backstop alone so the invariant
    # never goes fully dark.
    have_metadata, enabled_job_ids = _enabled_cron_index(ctx.get("cron_jobs"))
    now = datetime.now(timezone.utc)

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
        job_id = task_id.removeprefix("cron:")
        # Primary guard: only an enabled, currently-registered cron can be
        # "firing on schedule but failing". A disabled or deleted cron stops
        # appending runs, so its leading-failure count is frozen, not live.
        if have_metadata and job_id not in enabled_job_ids:
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
        # Backstop guard — DEGRADED PATH ONLY. When we have authoritative cron
        # metadata the enabled-and-known filter above is the precise guard, so
        # we must NOT also age-suppress a known-enabled cron (a monthly cron
        # can legitimately have a ~31-day-old head while failing every run).
        # Only when metadata is unavailable do we fall back to a recency cutoff
        # so an abandoned task can't freeze on the board.
        if not have_metadata:
            head_dt = _parse_started_at(data["last_started_at"])
            if head_dt is not None and (now - head_dt).total_seconds() > RECENCY_CUTOFF_SECONDS:
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
