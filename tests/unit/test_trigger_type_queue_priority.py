"""Tests for trigger_type queue prioritization and claim filtering.

Validates that the modified _sort_key ranks immediate tasks first,
and that claim_next_dispatch_tasks respects trigger_type filters.
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


class TestTriggerTypePriority:
    def test_immediate_ranks_before_heartbeat_poll(self):
        conn = _make_conn()
        _insert_task(conn, "hb_task", trigger_type="heartbeat_poll", priority=4)
        _insert_task(conn, "imm_task", trigger_type="immediate", priority=1)
        queue = task_hub.rebuild_dispatch_queue(conn)
        assert int(queue.get("eligible_total") or 0) >= 2
        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="test")
        assert claimed[0]["task_id"] == "imm_task"

    def test_immediate_ranks_before_scheduled(self):
        conn = _make_conn()
        _insert_task(conn, "sched", trigger_type="scheduled", priority=4, must_complete=True)
        _insert_task(conn, "imm", trigger_type="immediate", priority=1)
        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="test")
        assert claimed[0]["task_id"] == "imm"

    def test_trigger_type_filter_in_claim(self):
        conn = _make_conn()
        _insert_task(conn, "only_imm", trigger_type="immediate")
        _insert_task(conn, "only_hb", trigger_type="heartbeat_poll")
        claimed = task_hub.claim_next_dispatch_tasks(
            conn, limit=10, agent_id="test", trigger_types=["immediate"],
        )
        task_ids = {c["task_id"] for c in claimed}
        assert "only_imm" in task_ids
        assert "only_hb" not in task_ids

    def test_trigger_type_filter_none_returns_all(self):
        conn = _make_conn()
        _insert_task(conn, "a", trigger_type="immediate")
        _insert_task(conn, "b", trigger_type="heartbeat_poll")
        claimed = task_hub.claim_next_dispatch_tasks(
            conn, limit=10, agent_id="test", trigger_types=None,
        )
        task_ids = {c["task_id"] for c in claimed}
        assert "a" in task_ids
        assert "b" in task_ids

    def test_upsert_preserves_trigger_type(self):
        conn = _make_conn()
        _insert_task(conn, "tt_test", trigger_type="immediate")
        item = task_hub.get_item(conn, "tt_test")
        assert item is not None
        assert item["trigger_type"] == "immediate"

        # Re-upsert without trigger_type — should preserve existing
        task_hub.upsert_item(conn, {"task_id": "tt_test", "title": "Updated"})
        item2 = task_hub.get_item(conn, "tt_test")
        assert item2 is not None
        assert item2["trigger_type"] == "immediate"
