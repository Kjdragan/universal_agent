"""Tests for the VP SQLite → Redis outbound result bridge."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any
from unittest.mock import MagicMock

import pytest

from universal_agent.delegation.redis_bus import RedisMissionBus
from universal_agent.delegation.redis_vp_result_bridge import RedisVpResultBridge
from universal_agent.delegation.schema import MissionResultEnvelope
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import queue_vp_mission, finalize_vp_mission


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _mock_bus() -> MagicMock:
    bus = MagicMock(spec=RedisMissionBus)
    bus.publish_result.return_value = "result-msg-001"
    return bus


def _insert_bridge_mission(
    conn: sqlite3.Connection,
    *,
    mission_id: str = "bridge-test-001",
    vp_id: str = "vp.coder.primary",
    status: str = "queued",
    redis_job_id: str = "test-001",
) -> None:
    """Insert a mission as if the inbound bridge created it."""
    queue_vp_mission(
        conn,
        mission_id=mission_id,
        vp_id=vp_id,
        mission_type="coding_task",
        objective="test objective",
        payload={
            "task": "do the thing",
            "redis_job_id": redis_job_id,
        },
        source="redis_bridge",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRedisVpResultBridge:
    def test_publishes_completed_mission(self, db):
        bus = _mock_bus()
        _insert_bridge_mission(db, mission_id="bridge-c-001", redis_job_id="c-001")
        finalize_vp_mission(db, "bridge-c-001", "completed", result_ref="workspace://path")

        bridge = RedisVpResultBridge(bus, db, poll_seconds=1.0)
        # Run one tick directly
        published = bridge._tick()

        assert published == 1
        bus.publish_result.assert_called_once()
        call_args = bus.publish_result.call_args[0][0]
        assert isinstance(call_args, MissionResultEnvelope)
        assert call_args.job_id == "c-001"
        assert call_args.status == "SUCCESS"

        # Verify result_published flag was set
        row = db.execute(
            "SELECT result_published FROM vp_missions WHERE mission_id = ?",
            ("bridge-c-001",),
        ).fetchone()
        assert row["result_published"] == 1

    def test_publishes_failed_mission(self, db):
        bus = _mock_bus()
        _insert_bridge_mission(db, mission_id="bridge-f-001", redis_job_id="f-001")
        finalize_vp_mission(db, "bridge-f-001", "failed")

        bridge = RedisVpResultBridge(bus, db, poll_seconds=1.0)
        published = bridge._tick()

        assert published == 1
        call_args = bus.publish_result.call_args[0][0]
        assert call_args.job_id == "f-001"
        assert call_args.status == "FAILED"
        assert call_args.error is not None

    def test_does_not_double_publish(self, db):
        bus = _mock_bus()
        _insert_bridge_mission(db, mission_id="bridge-d-001", redis_job_id="d-001")
        finalize_vp_mission(db, "bridge-d-001", "completed")

        bridge = RedisVpResultBridge(bus, db, poll_seconds=1.0)
        # First tick publishes
        bridge._tick()
        bus.publish_result.reset_mock()
        # Second tick should find nothing
        published = bridge._tick()

        assert published == 0
        bus.publish_result.assert_not_called()

    def test_ignores_gateway_sourced_missions(self, db):
        bus = _mock_bus()
        # Insert a gateway-sourced mission (not from bridge)
        queue_vp_mission(
            db,
            mission_id="gateway-001",
            vp_id="vp.coder.primary",
            mission_type="coding_task",
            objective="gateway task",
            payload={"task": "local thing"},
            source="gateway",
        )
        finalize_vp_mission(db, "gateway-001", "completed")

        bridge = RedisVpResultBridge(bus, db, poll_seconds=1.0)
        published = bridge._tick()

        assert published == 0
        bus.publish_result.assert_not_called()

    def test_does_not_publish_queued_missions(self, db):
        bus = _mock_bus()
        _insert_bridge_mission(db, mission_id="bridge-q-001", redis_job_id="q-001")
        # Don't finalize — mission stays queued

        bridge = RedisVpResultBridge(bus, db, poll_seconds=1.0)
        published = bridge._tick()

        assert published == 0
        bus.publish_result.assert_not_called()

    def test_metrics_track_published_count(self, db):
        bus = _mock_bus()
        _insert_bridge_mission(db, mission_id="bridge-m-001", redis_job_id="m-001")
        finalize_vp_mission(db, "bridge-m-001", "completed")

        bridge = RedisVpResultBridge(bus, db, poll_seconds=1.0)
        bridge._tick()

        m = bridge.metrics
        assert m["published_total"] == 1
        assert m["errors_total"] == 0

    def test_fallback_job_id_from_mission_id(self, db):
        """If payload doesn't contain redis_job_id, strip bridge- prefix."""
        bus = _mock_bus()
        # Insert with payload missing redis_job_id
        queue_vp_mission(
            db,
            mission_id="bridge-fallback-001",
            vp_id="vp.general.primary",
            mission_type="general_task",
            objective="no redis_job_id in payload",
            payload={"task": "something"},
            source="redis_bridge",
        )
        finalize_vp_mission(db, "bridge-fallback-001", "completed")

        bridge = RedisVpResultBridge(bus, db, poll_seconds=1.0)
        bridge._tick()

        call_args = bus.publish_result.call_args[0][0]
        assert call_args.job_id == "fallback-001"
