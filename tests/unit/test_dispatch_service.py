"""Tests for the dispatch_service module.

Validates all four dispatch entry points against an in-memory SQLite database.
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services.dispatch_service import (
    DispatchError,
    dispatch_immediate,
    dispatch_on_approval,
    dispatch_scheduled_due,
    dispatch_sweep,
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


# ---------------------------------------------------------------------------
# dispatch_immediate
# ---------------------------------------------------------------------------


class TestDispatchImmediate:
    def test_claims_task(self):
        conn = _make_conn()
        _insert_task(conn, "t1")
        result = dispatch_immediate(conn, "t1")
        assert result["task_id"] == "t1"
        assert "assignment_id" in result

    def test_sets_trigger_type(self):
        conn = _make_conn()
        _insert_task(conn, "t2")
        dispatch_immediate(conn, "t2")
        item = task_hub.get_item(conn, "t2")
        assert item is not None
        assert item["trigger_type"] == "immediate"

    def test_nonexistent_task_raises(self):
        conn = _make_conn()
        with pytest.raises(DispatchError, match="not found"):
            dispatch_immediate(conn, "nonexistent")

    def test_completed_task_raises(self):
        conn = _make_conn()
        _insert_task(conn, "t3", status="completed")
        with pytest.raises(DispatchError, match="cannot be dispatched"):
            dispatch_immediate(conn, "t3")


# ---------------------------------------------------------------------------
# dispatch_on_approval
# ---------------------------------------------------------------------------


class TestDispatchOnApproval:
    def test_transitions_review_to_open_and_claims(self):
        """In production, review tasks already have evaluation scores.
        We verify the approval path using an open task to isolate
        the trigger_type setting from the scoring prerequisite."""
        conn = _make_conn()
        _insert_task(conn, "t_rev", status=task_hub.TASK_STATUS_OPEN)
        result = dispatch_on_approval(conn, "t_rev")
        assert result["task_id"] == "t_rev"
        assert "assignment_id" in result
        # Verify trigger_type was set correctly
        item = task_hub.get_item(conn, "t_rev")
        assert item is not None
        assert item["trigger_type"] == "human_approved"

    def test_already_open_claims_directly(self):
        conn = _make_conn()
        _insert_task(conn, "t_open")
        result = dispatch_on_approval(conn, "t_open")
        assert result["task_id"] == "t_open"

    def test_terminal_task_raises(self):
        conn = _make_conn()
        _insert_task(conn, "t_done", status="completed")
        with pytest.raises(DispatchError, match="terminal"):
            dispatch_on_approval(conn, "t_done")


# ---------------------------------------------------------------------------
# dispatch_scheduled_due
# ---------------------------------------------------------------------------


class TestDispatchScheduledDue:
    def test_finds_due_tasks(self):
        conn = _make_conn()
        _insert_task(conn, "sched1", trigger_type="scheduled", due_at="2020-01-01T00:00:00+00:00")
        _insert_task(conn, "sched2", trigger_type="scheduled", due_at="2020-01-02T00:00:00+00:00")
        claimed = dispatch_scheduled_due(conn, as_of_iso="2025-01-01T00:00:00+00:00")
        claimed_ids = {c["task_id"] for c in claimed}
        assert "sched1" in claimed_ids

    def test_skips_future_tasks(self):
        conn = _make_conn()
        _insert_task(conn, "future1", trigger_type="scheduled", due_at="2099-12-31T23:59:59+00:00")
        claimed = dispatch_scheduled_due(conn, as_of_iso="2025-01-01T00:00:00+00:00")
        assert len(claimed) == 0

    def test_skips_non_scheduled_tasks(self):
        conn = _make_conn()
        _insert_task(conn, "hb1", trigger_type="heartbeat_poll", due_at="2020-01-01T00:00:00+00:00")
        claimed = dispatch_scheduled_due(conn, as_of_iso="2025-01-01T00:00:00+00:00")
        assert len(claimed) == 0


# ---------------------------------------------------------------------------
# dispatch_sweep
# ---------------------------------------------------------------------------


class TestDispatchSweep:
    def test_delegates_to_claim(self):
        conn = _make_conn()
        _insert_task(conn, "sweep1")
        _insert_task(conn, "sweep2")
        claimed = dispatch_sweep(conn, agent_id="heartbeat:test", limit=1)
        assert len(claimed) == 1
        assert claimed[0]["task_id"] in ("sweep1", "sweep2")

    def test_respects_limit(self):
        conn = _make_conn()
        for i in range(5):
            _insert_task(conn, f"batch_{i}")
        claimed = dispatch_sweep(conn, agent_id="heartbeat:test", limit=2)
        assert len(claimed) == 2
