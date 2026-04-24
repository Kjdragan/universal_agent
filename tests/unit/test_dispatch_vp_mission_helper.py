"""Tests for the dispatch_vp_mission convenience wrapper and queue_proactive_task builder."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from universal_agent.services.proactive_task_builder import queue_proactive_task
from universal_agent import task_hub


class TestQueueProactiveTask:
    """Tests for the shared proactive task builder."""

    def _connect(self, tmp_path: Path) -> sqlite3.Connection:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        task_hub.ensure_schema(conn)
        return conn

    def test_creates_open_agent_ready_task(self, tmp_path: Path):
        with self._connect(tmp_path) as conn:
            item = queue_proactive_task(
                conn,
                task_id="test-123",
                source_kind="unit_test",
                source_ref="test",
                title="Test task",
                description="Test description",
                labels=["agent-ready", "test"],
            )
            assert item["task_id"] == "test-123"
            assert item["source_kind"] == "unit_test"
            assert item["project_key"] == "proactive"
            assert item["status"] == task_hub.TASK_STATUS_OPEN
            assert item["agent_ready"] is True
            assert item["trigger_type"] == "heartbeat_poll"

    def test_clamps_priority_to_valid_range(self, tmp_path: Path):
        with self._connect(tmp_path) as conn:
            item_high = queue_proactive_task(
                conn,
                task_id="test-high",
                source_kind="unit_test",
                source_ref="test",
                title="High priority",
                description="",
                priority=99,
            )
            assert item_high["priority"] == 4

            item_low = queue_proactive_task(
                conn,
                task_id="test-low",
                source_kind="unit_test",
                source_ref="test",
                title="Low priority",
                description="",
                priority=-5,
            )
            assert item_low["priority"] == 1

    def test_stores_metadata(self, tmp_path: Path):
        with self._connect(tmp_path) as conn:
            item = queue_proactive_task(
                conn,
                task_id="test-meta",
                source_kind="unit_test",
                source_ref="test",
                title="Meta task",
                description="",
                metadata={"source": "test", "theme": "cleanup"},
            )
            assert item["metadata"]["source"] == "test"
            assert item["metadata"]["theme"] == "cleanup"


class TestDispatchVpMissionHelper:
    """Tests for dispatch_vp_mission convenience wrapper."""

    def test_import_succeeds(self):
        from universal_agent.tools.vp_orchestration import dispatch_vp_mission
        assert callable(dispatch_vp_mission)
