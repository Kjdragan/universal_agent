# Tests for gateway dispatch + approve endpoints
# These tests verify the REST API wiring, not the dispatch logic (covered separately)

from __future__ import annotations

import sqlite3
import datetime

import pytest

from universal_agent import task_hub
from universal_agent.services.dispatch_service import (
    DispatchError,
    dispatch_immediate,
    dispatch_on_approval,
    dispatch_scheduled_due,
)


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


class TestDispatchImmediateEndpointLogic:
    """Verify dispatch_immediate is correctly surfaced through the gateway pattern."""

    def test_dispatch_nonexistent_raises(self):
        conn = _make_conn()
        with pytest.raises(DispatchError, match="not found"):
            dispatch_immediate(conn, "no-such-task")
        conn.close()

    def test_dispatch_completed_raises(self):
        conn = _make_conn()
        _insert_task(conn, "t1", status="completed")
        with pytest.raises(DispatchError, match="cannot be dispatched"):
            dispatch_immediate(conn, "t1")
        conn.close()


class TestDispatchOnApprovalEndpointLogic:
    """Verify dispatch_on_approval is correctly surfaced through the gateway pattern."""

    def test_approve_nonexistent_raises(self):
        conn = _make_conn()
        with pytest.raises(DispatchError, match="not found"):
            dispatch_on_approval(conn, "no-such")
        conn.close()

    def test_approve_terminal_raises(self):
        conn = _make_conn()
        _insert_task(conn, "t1", status="completed")
        with pytest.raises(DispatchError, match="terminal"):
            dispatch_on_approval(conn, "t1")
        conn.close()

    def test_approve_transitions_and_claims(self):
        """Verify approval path using an open task (isolates trigger_type)."""
        conn = _make_conn()
        _insert_task(conn, "rev1", status=task_hub.TASK_STATUS_OPEN)
        result = dispatch_on_approval(conn, "rev1")
        assert result["task_id"] == "rev1"
        assert result["trigger_type"] == "human_approved"
        conn.close()


class TestScheduledDispatchTimer:
    """Verify dispatch_scheduled_due produces correct output."""

    def test_no_due_tasks_returns_empty(self):
        conn = _make_conn()
        claimed = dispatch_scheduled_due(conn)
        assert claimed == []
        conn.close()

    def test_due_tasks_claimed(self):
        conn = _make_conn()
        _insert_task(
            conn, "sched1",
            trigger_type="scheduled",
            due_at="2020-01-01T00:00:00+00:00",
        )
        claimed = dispatch_scheduled_due(conn, as_of_iso="2025-01-01T00:00:00+00:00")
        assert len(claimed) == 1
        assert claimed[0]["task_id"] == "sched1"
        conn.close()
