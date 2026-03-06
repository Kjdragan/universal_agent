"""Tests for the Redis→VP SQLite inbound bridge."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from universal_agent.delegation.redis_bus import ConsumedMission, RedisMissionBus
from universal_agent.delegation.redis_vp_bridge import (
    MISSION_KIND_TO_VP,
    BridgeConfig,
    RedisVpBridge,
    _SKIP_KINDS,
)
from universal_agent.delegation.schema import MissionEnvelope, MissionPayload
from universal_agent.durable.migrations import ensure_schema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _make_envelope(
    *,
    job_id: str = "test-job-001",
    task: str = "implement feature X",
    mission_kind: str = "coding_task",
    extra_context: dict[str, Any] | None = None,
) -> MissionEnvelope:
    context: dict[str, Any] = {"mission_kind": mission_kind}
    if extra_context:
        context.update(extra_context)
    return MissionEnvelope(
        job_id=job_id,
        idempotency_key=f"idem-{job_id}",
        priority=50,
        timeout_seconds=600,
        max_retries=2,
        payload=MissionPayload(task=task, context=context),
    )


def _make_consumed(
    envelope: MissionEnvelope,
    message_id: str = "1234-0",
) -> ConsumedMission:
    return ConsumedMission(
        stream="ua:missions:delegation",
        message_id=message_id,
        envelope=envelope,
        raw={"envelope": json.dumps(envelope.model_dump(mode="json"))},
    )


def _mock_bus(consumed: list[ConsumedMission] | None = None) -> MagicMock:
    bus = MagicMock(spec=RedisMissionBus)
    bus.consume.return_value = consumed or []
    bus.ack.return_value = 1
    bus.fail_and_maybe_dlq.return_value = True
    bus.publish_mission.return_value = "retry-msg-id"
    return bus


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBridgeConfig:
    def test_resolve_consumer_name_default(self):
        cfg = BridgeConfig()
        name = cfg.resolve_consumer_name("my-host")
        assert name.startswith("bridge_my-host")

    def test_resolve_consumer_name_explicit(self):
        cfg = BridgeConfig(consumer_name="custom-consumer")
        assert cfg.resolve_consumer_name("ignored") == "custom-consumer"


class TestMissionKindRouting:
    def test_coding_task_routes_to_coder(self):
        assert MISSION_KIND_TO_VP["coding_task"] == "vp.coder.primary"

    def test_general_task_routes_to_generalist(self):
        assert MISSION_KIND_TO_VP["general_task"] == "vp.general.primary"

    def test_tutorial_bootstrap_is_skipped(self):
        assert "tutorial_bootstrap_repo" in _SKIP_KINDS


class TestRedisVpBridge:
    def test_inserts_coding_mission(self, db):
        envelope = _make_envelope(mission_kind="coding_task")
        consumed = _make_consumed(envelope)
        bus = _mock_bus([consumed])

        bridge = RedisVpBridge(bus, db, BridgeConfig())
        inserted = asyncio.get_event_loop().run_until_complete(bridge.run(once=True))

        assert inserted == 1
        bus.ack.assert_called_once_with("1234-0")

        row = db.execute(
            "SELECT * FROM vp_missions WHERE mission_id = ?",
            ("bridge-test-job-001",),
        ).fetchone()
        assert row is not None
        assert row["vp_id"] == "vp.coder.primary"
        assert row["status"] == "queued"
        assert row["source"] == "redis_bridge"

    def test_inserts_general_mission(self, db):
        envelope = _make_envelope(mission_kind="general_task", job_id="gen-001")
        consumed = _make_consumed(envelope, message_id="5678-0")
        bus = _mock_bus([consumed])

        bridge = RedisVpBridge(bus, db, BridgeConfig())
        asyncio.get_event_loop().run_until_complete(bridge.run(once=True))

        row = db.execute(
            "SELECT * FROM vp_missions WHERE mission_id = ?",
            ("bridge-gen-001",),
        ).fetchone()
        assert row is not None
        assert row["vp_id"] == "vp.general.primary"

    def test_unknown_kind_defaults_to_general(self, db):
        envelope = _make_envelope(mission_kind="unknown_fancy_kind", job_id="unk-001")
        consumed = _make_consumed(envelope, message_id="9999-0")
        bus = _mock_bus([consumed])

        bridge = RedisVpBridge(bus, db, BridgeConfig())
        asyncio.get_event_loop().run_until_complete(bridge.run(once=True))

        row = db.execute(
            "SELECT * FROM vp_missions WHERE mission_id = ?",
            ("bridge-unk-001",),
        ).fetchone()
        assert row is not None
        assert row["vp_id"] == "vp.general.primary"

    def test_skips_tutorial_bootstrap_repo(self, db):
        envelope = _make_envelope(mission_kind="tutorial_bootstrap_repo", job_id="tut-001")
        consumed = _make_consumed(envelope, message_id="tut-msg-0")
        bus = _mock_bus([consumed])

        bridge = RedisVpBridge(bus, db, BridgeConfig())
        inserted = asyncio.get_event_loop().run_until_complete(bridge.run(once=True))

        assert inserted == 0
        bus.ack.assert_called_once_with("tut-msg-0")
        row = db.execute(
            "SELECT * FROM vp_missions WHERE mission_id = ?",
            ("bridge-tut-001",),
        ).fetchone()
        assert row is None

    def test_idempotent_duplicate_acked(self, db):
        envelope = _make_envelope(job_id="dup-001")
        consumed = _make_consumed(envelope, message_id="dup-msg-0")
        bus = _mock_bus([consumed])

        bridge = RedisVpBridge(bus, db, BridgeConfig())
        # First insertion
        asyncio.get_event_loop().run_until_complete(bridge.run(once=True))
        # Second insertion — same mission
        bus.consume.return_value = [consumed]
        bus.ack.reset_mock()
        asyncio.get_event_loop().run_until_complete(bridge.run(once=True))

        # Should ack without inserting a duplicate
        bus.ack.assert_called_with("dup-msg-0")
        rows = db.execute(
            "SELECT COUNT(*) AS cnt FROM vp_missions WHERE mission_id = ?",
            ("bridge-dup-001",),
        ).fetchone()
        assert rows["cnt"] == 1

    def test_empty_consume_returns_zero(self, db):
        bus = _mock_bus([])
        bridge = RedisVpBridge(bus, db, BridgeConfig())
        inserted = asyncio.get_event_loop().run_until_complete(bridge.run(once=True))
        assert inserted == 0

    def test_insertion_failure_triggers_dlq(self, db):
        envelope = _make_envelope(job_id="fail-001")
        consumed = _make_consumed(envelope, message_id="fail-msg-0")
        bus = _mock_bus([consumed])

        bridge = RedisVpBridge(bus, db, BridgeConfig())

        # Force queue_vp_mission to fail
        with patch(
            "universal_agent.delegation.redis_vp_bridge.queue_vp_mission",
            side_effect=RuntimeError("DB locked"),
        ):
            asyncio.get_event_loop().run_until_complete(bridge.run(once=True))

        bus.fail_and_maybe_dlq.assert_called_once()

    def test_metrics_track_counts(self, db):
        envelope = _make_envelope(job_id="met-001")
        consumed = _make_consumed(envelope, message_id="met-msg-0")
        bus = _mock_bus([consumed])

        bridge = RedisVpBridge(bus, db, BridgeConfig())
        asyncio.get_event_loop().run_until_complete(bridge.run(once=True))

        m = bridge.metrics
        assert m["consumed_total"] == 1
        assert m["inserted_total"] == 1
        assert m["errors_total"] == 0
