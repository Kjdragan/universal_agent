"""Tests for ``POST /api/v1/dashboard/todolist/bulk-park``.

Pin the lane→status mapping so the bulk endpoint targets exactly the same
items the dashboard renders in each Kanban lane. Pre-fix, the dashboard
iterated visible rows client-side and only parked the rendered window
(limit=120) — when 500+ items were queued, the operator had to click
"Park all" multiple times. The new endpoint resolves candidates server-side
in one round-trip.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from universal_agent import task_hub
from universal_agent.gateway_server import _BULK_PARK_LANE_QUERIES


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed(conn: sqlite3.Connection, task_id: str, status: str) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "dashboard_quick_add",
            "title": f"task {task_id}",
            "description": "",
            "status": status,
        },
    )


def _seed_assignment(conn: sqlite3.Connection, task_id: str, state: str) -> None:
    conn.execute(
        """
        INSERT INTO task_hub_assignments (assignment_id, task_id, agent_id, state, started_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            f"asg_{task_id}",
            task_id,
            "todo:daemon_simone_todo",
            state,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _matching_ids(conn: sqlite3.Connection, lane: str) -> list[str]:
    rows = conn.execute(_BULK_PARK_LANE_QUERIES[lane]).fetchall()
    return [str(r["task_id"]) for r in rows]


def test_not_assigned_lane_matches_open_and_delegated_without_active_assignment():
    conn = _conn()
    _seed(conn, "t-open", task_hub.TASK_STATUS_OPEN)
    _seed(conn, "t-delegated-idle", task_hub.TASK_STATUS_DELEGATED)
    _seed(conn, "t-delegated-active", task_hub.TASK_STATUS_DELEGATED)
    _seed_assignment(conn, "t-delegated-active", "seized")
    _seed(conn, "t-blocked", task_hub.TASK_STATUS_BLOCKED)
    _seed(conn, "t-completed", task_hub.TASK_STATUS_COMPLETED)
    _seed(conn, "t-parked", task_hub.TASK_STATUS_PARKED)

    ids = sorted(_matching_ids(conn, "not_assigned"))

    assert ids == sorted(["t-open", "t-delegated-idle"]), (
        "not_assigned must include status='open' and status='delegated' WITHOUT "
        "an active (seized/running) assignment, and must NOT pull in blocked/"
        "completed/parked tasks or delegated tasks that ARE actively claimed."
    )


def test_in_progress_lane_matches_status_or_active_assignment():
    conn = _conn()
    _seed(conn, "t-in-progress", task_hub.TASK_STATUS_IN_PROGRESS)
    _seed(conn, "t-delegated-running", task_hub.TASK_STATUS_DELEGATED)
    _seed_assignment(conn, "t-delegated-running", "running")
    _seed(conn, "t-delegated-idle", task_hub.TASK_STATUS_DELEGATED)
    _seed(conn, "t-open", task_hub.TASK_STATUS_OPEN)

    ids = sorted(_matching_ids(conn, "in_progress"))
    assert ids == sorted(["t-in-progress", "t-delegated-running"])


def test_blocked_lane_matches_blocked_only():
    conn = _conn()
    _seed(conn, "t-blocked-1", task_hub.TASK_STATUS_BLOCKED)
    _seed(conn, "t-blocked-2", task_hub.TASK_STATUS_BLOCKED)
    _seed(conn, "t-open", task_hub.TASK_STATUS_OPEN)

    ids = sorted(_matching_ids(conn, "blocked"))
    assert ids == sorted(["t-blocked-1", "t-blocked-2"])


def test_needs_review_lane_includes_needs_review_and_pending_review():
    conn = _conn()
    _seed(conn, "t-review", task_hub.TASK_STATUS_REVIEW)
    _seed(conn, "t-pending-review", task_hub.TASK_STATUS_PENDING_REVIEW)
    _seed(conn, "t-open", task_hub.TASK_STATUS_OPEN)

    ids = sorted(_matching_ids(conn, "needs_review"))
    assert ids == sorted(["t-review", "t-pending-review"])


def test_bulk_park_flow_parks_all_not_assigned_in_one_pass():
    """Functional sanity: feed N items, run perform_task_action(park) for
    each candidate, confirm all flip to parked in a single connection."""
    conn = _conn()
    open_ids = [f"t-open-{i:03d}" for i in range(50)]
    for tid in open_ids:
        _seed(conn, tid, task_hub.TASK_STATUS_OPEN)
    _seed(conn, "t-completed", task_hub.TASK_STATUS_COMPLETED)
    _seed(conn, "t-parked", task_hub.TASK_STATUS_PARKED)

    candidates = _matching_ids(conn, "not_assigned")
    assert sorted(candidates) == sorted(open_ids)

    for tid in candidates:
        task_hub.perform_task_action(
            conn,
            task_id=tid,
            action="park",
            reason="bulk_park_not_assigned",
            agent_id="dashboard_operator",
        )

    statuses = {
        row["task_id"]: row["status"]
        for row in conn.execute(
            "SELECT task_id, status FROM task_hub_items WHERE task_id LIKE 't-open-%'"
        )
    }
    assert all(s == task_hub.TASK_STATUS_PARKED for s in statuses.values()), statuses
    # Unrelated terminal rows must not be touched.
    completed_row = conn.execute(
        "SELECT status FROM task_hub_items WHERE task_id='t-completed'"
    ).fetchone()
    assert completed_row["status"] == task_hub.TASK_STATUS_COMPLETED
