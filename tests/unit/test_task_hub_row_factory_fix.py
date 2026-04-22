"""Tests verifying task_hub functions work correctly regardless of row_factory.

These tests exercise the bug fix for dict(row) hydration crashes that occurred
when a raw sqlite3.Connection (without row_factory=sqlite3.Row) was passed to
task_hub functions.
"""
from __future__ import annotations

import sqlite3

from universal_agent import task_hub


def _raw_conn() -> sqlite3.Connection:
    """Return a bare in-memory connection WITHOUT row_factory set."""
    conn = sqlite3.connect(":memory:")
    # Explicitly NOT setting conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _row_conn() -> sqlite3.Connection:
    """Return a connection WITH row_factory set (the historical pattern)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_task(conn: sqlite3.Connection, task_id: str = "test-001") -> dict:
    return task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "manual",
            "source_ref": "test",
            "title": "Test task",
            "description": "Test description",
            "project_key": "test",
            "priority": 2,
            "labels": ["test"],
            "status": task_hub.TASK_STATUS_OPEN,
        },
    )


# -----------------------------------------------------------------------
# get_item -- the primary crash site from the original bug report
# -----------------------------------------------------------------------


def test_get_item_raw_connection_returns_dict() -> None:
    """get_item must return a proper dict even without row_factory."""
    conn = _raw_conn()
    try:
        _seed_task(conn, "raw-001")
        result = task_hub.get_item(conn, "raw-001")
        assert result is not None
        assert isinstance(result, dict)
        assert result["task_id"] == "raw-001"
        assert result["title"] == "Test task"
    finally:
        conn.close()


def test_get_item_row_factory_connection_still_works() -> None:
    """get_item must still work when row_factory IS set (regression guard)."""
    conn = _row_conn()
    try:
        _seed_task(conn, "row-001")
        result = task_hub.get_item(conn, "row-001")
        assert result is not None
        assert isinstance(result, dict)
        assert result["task_id"] == "row-001"
    finally:
        conn.close()


def test_get_item_nonexistent_returns_none() -> None:
    conn = _raw_conn()
    try:
        assert task_hub.get_item(conn, "no-such-id") is None
    finally:
        conn.close()


# -----------------------------------------------------------------------
# list functions that iterate rows with dict(row)
# -----------------------------------------------------------------------


def test_rebuild_dispatch_queue_raw_connection() -> None:
    conn = _raw_conn()
    try:
        _seed_task(conn, "dispatch-001")
        result = task_hub.rebuild_dispatch_queue(conn)
        assert isinstance(result, dict)
        assert "queue_build_id" in result
        assert result["items_total"] >= 1
    finally:
        conn.close()


def test_list_due_scheduled_tasks_raw_connection() -> None:
    conn = _raw_conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "sched-001",
                "source_kind": "system_command",
                "source_ref": "test",
                "title": "Scheduled task",
                "description": "Due already",
                "project_key": "test",
                "priority": 1,
                "status": task_hub.TASK_STATUS_OPEN,
                "trigger_type": "scheduled",
                "due_at": "2020-01-01T00:00:00+00:00",
            },
        )
        items = task_hub.list_due_scheduled_tasks(conn, as_of_iso="2099-01-01T00:00:00+00:00")
        assert isinstance(items, list)
        assert len(items) == 1
        assert items[0]["task_id"] == "sched-001"
    finally:
        conn.close()


def test_list_completed_tasks_raw_connection() -> None:
    conn = _raw_conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "done-001",
                "source_kind": "manual",
                "source_ref": "test",
                "title": "Done task",
                "description": "",
                "project_key": "test",
                "priority": 1,
                "status": task_hub.TASK_STATUS_COMPLETED,
            },
        )
        items = task_hub.list_completed_tasks(conn)
        assert isinstance(items, list)
        assert any(i["task_id"] == "done-001" for i in items)
    finally:
        conn.close()


def test_list_personal_queue_raw_connection() -> None:
    conn = _raw_conn()
    try:
        _seed_task(conn, "pq-001")
        items = task_hub.list_personal_queue(conn)
        assert isinstance(items, list)
    finally:
        conn.close()


def test_list_subtasks_raw_connection() -> None:
    conn = _raw_conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "parent-001",
                "source_kind": "manual",
                "source_ref": "test",
                "title": "Parent task",
                "description": "",
                "project_key": "test",
                "priority": 1,
                "status": task_hub.TASK_STATUS_OPEN,
            },
        )
        task_hub.upsert_item(
            conn,
            {
                "task_id": "child-001",
                "source_kind": "manual",
                "source_ref": "test",
                "title": "Child task",
                "description": "",
                "project_key": "test",
                "priority": 1,
                "status": task_hub.TASK_STATUS_OPEN,
                "parent_task_id": "parent-001",
            },
        )
        children = task_hub.list_subtasks(conn, "parent-001")
        assert isinstance(children, list)
        assert len(children) == 1
        assert children[0]["task_id"] == "child-001"
    finally:
        conn.close()


# -----------------------------------------------------------------------
# ensure_schema sets row_factory -- the actual fix mechanism
# -----------------------------------------------------------------------


def test_ensure_schema_sets_row_factory() -> None:
    """ensure_schema should set row_factory so subsequent fetches work."""
    conn = sqlite3.connect(":memory:")
    assert conn.row_factory is None  # default
    task_hub.ensure_schema(conn)
    assert conn.row_factory is sqlite3.Row
    conn.close()


# -----------------------------------------------------------------------
# Question queue functions that use dict(row)
# -----------------------------------------------------------------------


def test_question_queue_raw_connection() -> None:
    conn = _raw_conn()
    try:
        _seed_task(conn, "q-001")
        q = task_hub.enqueue_question(
            conn, task_id="q-001", question_text="Is it done?", channel="dashboard"
        )
        assert q["question_id"]

        pending = task_hub.list_pending_questions(conn)
        assert len(pending) >= 1

        answered = task_hub.answer_question(
            conn, question_id=q["question_id"], answer_text="Yes"
        )
        assert answered["answered"] == 1 or answered.get("answered") is True

        expiring = task_hub.list_expiring_questions(conn)
        assert isinstance(expiring, list)
    finally:
        conn.close()


# -----------------------------------------------------------------------
# Comments -- uses dict(row) in list_comments
# -----------------------------------------------------------------------


def test_list_comments_raw_connection() -> None:
    conn = _raw_conn()
    try:
        _seed_task(conn, "comment-001")
        task_hub.add_comment(
            conn,
            task_id="comment-001",
            author="tester",
            content="A comment",
        )
        comments = task_hub.list_comments(conn, "comment-001")
        assert len(comments) >= 1
        assert comments[0]["content"] == "A comment"
    finally:
        conn.close()


