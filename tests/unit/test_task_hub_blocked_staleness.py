"""Unit tests for the blocked-task staleness surfacing branch.

Background: ``_apply_stale_policy`` demotes any aging non-terminal task to
``parked``. ``blocked`` is NOT terminal, so with staleness enabled a stuck
blocked task used to be *parked* — the wrong outcome. A task stuck in
``blocked`` past the threshold should be SURFACED for a human (it needs
unblocking), never silently parked.

The fix adds a distinct ``blocked`` branch that emits the surfacing
``stale_state`` ``STALE_STATE_BLOCKED_SURFACED`` and forces NO status change
(force_status stays ``None``), and only when ``UA_TASK_STALE_ENABLED`` is on.
These tests pin that contract.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _policy(*, stale_enabled: bool) -> task_hub.TaskHubPolicy:
    return task_hub.TaskHubPolicy(
        agent_threshold=3,
        stale_enabled=stale_enabled,
        stale_min_cycles=4,
        stale_min_age_minutes=180,
    )


def _blocked_task_past_threshold() -> dict:
    """A blocked task old enough (age) and cycled enough to be stale."""
    old = (datetime.now(timezone.utc) - timedelta(minutes=600)).isoformat()
    return {
        "task_id": "t-blocked-stale",
        "status": task_hub.TASK_STATUS_BLOCKED,
        "created_at": old,
        # one increment away from the min_cycles threshold (4)
        "metadata": {"stale_missed_cycles": 3},
    }


def test_blocked_past_threshold_is_surfaced_not_parked() -> None:
    conn = _conn()
    stale_state, metadata, force_status = task_hub._apply_stale_policy(
        conn, _blocked_task_past_threshold(), _policy(stale_enabled=True)
    )
    # Surfacing state, NOT a park: force_status must be None so status stays blocked.
    assert stale_state == task_hub.STALE_STATE_BLOCKED_SURFACED
    assert force_status is None
    assert force_status != task_hub.TASK_STATUS_PARKED
    # cycle counter advanced (kept, not reset) so it keeps aging.
    assert metadata["stale_missed_cycles"] == 4


def test_blocked_untouched_when_staleness_disabled() -> None:
    conn = _conn()
    task = _blocked_task_past_threshold()
    task["stale_state"] = "fresh"
    stale_state, _metadata, force_status = task_hub._apply_stale_policy(
        conn, task, _policy(stale_enabled=False)
    )
    # Flag off => existing stale_state preserved, no forced status.
    assert stale_state == "fresh"
    assert force_status is None


def test_blocked_below_threshold_ages_but_is_not_surfaced() -> None:
    conn = _conn()
    fresh = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    task = {
        "task_id": "t-blocked-young",
        "status": task_hub.TASK_STATUS_BLOCKED,
        "created_at": fresh,
        "metadata": {"stale_missed_cycles": 0},
    }
    stale_state, _metadata, force_status = task_hub._apply_stale_policy(
        conn, task, _policy(stale_enabled=True)
    )
    assert stale_state == "aging"
    assert force_status is None
