"""Regression: get_agent_activity excludes assignments for terminal-status items.

2026-05-18 13:23 incident: a proactive-codie cleanup task was serviced by
Cody via PR #344 — the underlying task_hub_items row moved to
``status=completed``, but the corresponding task_hub_assignments row
was left dangling in state ``seized``/``running``. ``get_agent_activity``
JOINed only on task_id, so the stale assignment surfaced as an ACTIVE
ASSIGNMENT in Simone's prompt. When Simone tried to act on the now-
terminal task, the lifecycle validator rejected it, leaving her with
zero tool calls and triggering "Execution Missing Lifecycle Mutation".

This test pins the filter: assignments whose backing item is terminal
(completed/parked/cancelled) must not appear in get_agent_activity.
"""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_item(conn: sqlite3.Connection, *, task_id: str, status: str) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "proactive_codie",
            "title": f"Task {task_id}",
            "project_key": "code",
            "agent_ready": True,
            "status": status,
        },
    )


def _seed_assignment(
    conn: sqlite3.Connection,
    *,
    assignment_id: str,
    task_id: str,
    state: str,
    agent_id: str = "todo:daemon_simone_todo",
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn.execute(
        """
        INSERT INTO task_hub_assignments (
            assignment_id, task_id, agent_id, state, started_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (assignment_id, task_id, agent_id, state, now),
    )
    conn.commit()


def test_active_assignments_excludes_terminal_items() -> None:
    """Assignment in 'seized' against an item in 'completed' must NOT appear."""
    conn = _conn()
    try:
        _seed_item(conn, task_id="proactive-codie:stale-1", status="completed")
        _seed_assignment(
            conn,
            assignment_id="a1",
            task_id="proactive-codie:stale-1",
            state="seized",
        )

        activity = task_hub.get_agent_activity(conn)
        active = activity.get("active_assignments") or []
        task_ids = [str(a.get("task_id")) for a in active]
        assert "proactive-codie:stale-1" not in task_ids, (
            f"Stale assignment for completed item leaked into active list: {task_ids}"
        )
    finally:
        conn.close()


def test_active_assignments_excludes_parked_items() -> None:
    conn = _conn()
    try:
        _seed_item(conn, task_id="task:parked-1", status="parked")
        _seed_assignment(
            conn, assignment_id="a2", task_id="task:parked-1", state="running"
        )

        activity = task_hub.get_agent_activity(conn)
        task_ids = [
            str(a.get("task_id")) for a in (activity.get("active_assignments") or [])
        ]
        assert "task:parked-1" not in task_ids
    finally:
        conn.close()


def test_active_assignments_excludes_cancelled_items() -> None:
    conn = _conn()
    try:
        _seed_item(conn, task_id="task:cancelled-1", status="cancelled")
        _seed_assignment(
            conn, assignment_id="a3", task_id="task:cancelled-1", state="running"
        )

        activity = task_hub.get_agent_activity(conn)
        task_ids = [
            str(a.get("task_id")) for a in (activity.get("active_assignments") or [])
        ]
        assert "task:cancelled-1" not in task_ids
    finally:
        conn.close()


def test_active_assignments_includes_in_progress_items() -> None:
    """Genuine in-progress assignments must still appear."""
    conn = _conn()
    try:
        _seed_item(conn, task_id="task:live-1", status="in_progress")
        _seed_assignment(
            conn, assignment_id="a4", task_id="task:live-1", state="running"
        )

        activity = task_hub.get_agent_activity(conn)
        task_ids = [
            str(a.get("task_id")) for a in (activity.get("active_assignments") or [])
        ]
        assert "task:live-1" in task_ids


    finally:
        conn.close()


def test_active_assignments_includes_open_items() -> None:
    conn = _conn()
    try:
        _seed_item(conn, task_id="task:open-1", status="open")
        _seed_assignment(
            conn, assignment_id="a5", task_id="task:open-1", state="seized"
        )

        activity = task_hub.get_agent_activity(conn)
        task_ids = [
            str(a.get("task_id")) for a in (activity.get("active_assignments") or [])
        ]
        assert "task:open-1" in task_ids
    finally:
        conn.close()


def test_terminal_statuses_constant_covers_known_values() -> None:
    """If a new terminal status is added, the filter list must learn about it."""
    assert task_hub.TASK_STATUS_COMPLETED in task_hub.TERMINAL_STATUSES
    assert task_hub.TASK_STATUS_PARKED in task_hub.TERMINAL_STATUSES
    assert task_hub.TASK_STATUS_CANCELLED in task_hub.TERMINAL_STATUSES
