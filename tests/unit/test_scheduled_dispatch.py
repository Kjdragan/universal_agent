"""Tests for the list_due_scheduled_tasks helper.

Validates that the function correctly filters by trigger_type, due_at, and status.
"""

from __future__ import annotations

import sqlite3

from universal_agent import task_hub


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _insert_task(conn: sqlite3.Connection, task_id: str, **overrides) -> dict:
    item = {
        "task_id": task_id,
        "title": f"Test {task_id}",
        "status": task_hub.TASK_STATUS_OPEN,
        "source_kind": "internal",
        "agent_ready": True,
        "labels": ["agent-ready"],
    }
    item.update(overrides)
    return task_hub.upsert_item(conn, item)


class TestListDueScheduledTasks:
    def test_returns_past_due(self):
        conn = _make_conn()
        _insert_task(conn, "past", trigger_type="scheduled", due_at="2020-06-01T00:00:00+00:00")
        result = task_hub.list_due_scheduled_tasks(conn, as_of_iso="2025-01-01T00:00:00+00:00")
        assert len(result) == 1
        assert result[0]["task_id"] == "past"

    def test_excludes_future(self):
        conn = _make_conn()
        _insert_task(conn, "future", trigger_type="scheduled", due_at="2099-12-31T23:59:59+00:00")
        result = task_hub.list_due_scheduled_tasks(conn, as_of_iso="2025-01-01T00:00:00+00:00")
        assert len(result) == 0

    def test_excludes_non_scheduled(self):
        conn = _make_conn()
        _insert_task(conn, "hb", trigger_type="heartbeat_poll", due_at="2020-01-01T00:00:00+00:00")
        result = task_hub.list_due_scheduled_tasks(conn, as_of_iso="2025-01-01T00:00:00+00:00")
        assert len(result) == 0

    def test_excludes_non_open(self):
        conn = _make_conn()
        _insert_task(conn, "done", trigger_type="scheduled", due_at="2020-01-01T00:00:00+00:00", status="completed")
        result = task_hub.list_due_scheduled_tasks(conn, as_of_iso="2025-01-01T00:00:00+00:00")
        assert len(result) == 0

    def test_multiple_due_ordered_by_due_at(self):
        conn = _make_conn()
        _insert_task(conn, "later", trigger_type="scheduled", due_at="2020-06-01T00:00:00+00:00")
        _insert_task(conn, "earlier", trigger_type="scheduled", due_at="2020-01-01T00:00:00+00:00")
        result = task_hub.list_due_scheduled_tasks(conn, as_of_iso="2025-01-01T00:00:00+00:00")
        assert len(result) == 2
        assert result[0]["task_id"] == "earlier"
        assert result[1]["task_id"] == "later"
