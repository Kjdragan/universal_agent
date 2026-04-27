"""Tests for the SQLite-backed factory registration store."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
import time

import pytest

from universal_agent.delegation.factory_registry import (
    OFFLINE_THRESHOLD_SECONDS,
    STALE_THRESHOLD_SECONDS,
    FactoryRegistry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _in_memory_registry() -> FactoryRegistry:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return FactoryRegistry(conn)


def _base_payload(**overrides) -> dict:
    defaults = {
        "factory_id": "test-desktop",
        "factory_role": "LOCAL_WORKER",
        "deployment_profile": "local_workstation",
        "registration_status": "online",
        "heartbeat_latency_ms": 42.5,
        "capabilities": ["delegation_redis", "vp_coder"],
        "metadata": {"hostname": "test-desktop", "pid": 1234},
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

class TestUpsert:
    def test_insert_new_factory(self):
        reg = _in_memory_registry()
        record = reg.upsert(_base_payload(), source="api_registration")
        assert record["factory_id"] == "test-desktop"
        assert record["factory_role"] == "LOCAL_WORKER"
        assert record["source"] == "api_registration"
        assert record["registration_status"] == "online"
        assert record["heartbeat_latency_ms"] == 42.5
        assert record["capabilities"] == ["delegation_redis", "vp_coder"]
        assert record["first_seen_at"] == record["last_seen_at"]

    def test_upsert_preserves_first_seen(self):
        reg = _in_memory_registry()
        r1 = reg.upsert(_base_payload(), source="first")
        first_seen = r1["first_seen_at"]

        # Second upsert should preserve first_seen_at
        r2 = reg.upsert(_base_payload(heartbeat_latency_ms=100.0), source="second")
        assert r2["first_seen_at"] == first_seen
        assert r2["source"] == "second"
        assert r2["heartbeat_latency_ms"] == 100.0

    def test_upsert_updates_last_seen(self):
        reg = _in_memory_registry()
        r1 = reg.upsert(_base_payload(), source="first")
        # Tiny delay to ensure timestamps differ
        import time; time.sleep(0.01)
        r2 = reg.upsert(_base_payload(), source="second")
        assert r2["last_seen_at"] >= r1["last_seen_at"]

    def test_upsert_requires_factory_id(self):
        reg = _in_memory_registry()
        with pytest.raises(ValueError, match="factory_id is required"):
            reg.upsert({"factory_role": "LOCAL_WORKER"}, source="test")

    def test_upsert_defaults_for_missing_fields(self):
        reg = _in_memory_registry()
        record = reg.upsert(
            {"factory_id": "minimal"},
            source="test",
        )
        assert record["factory_role"] == "UNKNOWN"
        assert record["deployment_profile"] == "local_workstation"
        assert record["registration_status"] == "online"
        assert record["capabilities"] == []
        assert record["metadata"] == {}


# ---------------------------------------------------------------------------
# Get / List
# ---------------------------------------------------------------------------

class TestGetAndList:
    def test_get_existing(self):
        reg = _in_memory_registry()
        reg.upsert(_base_payload(factory_id="f1"), source="test")
        result = reg.get("f1")
        assert result is not None
        assert result["factory_id"] == "f1"

    def test_get_missing(self):
        reg = _in_memory_registry()
        assert reg.get("nonexistent") is None

    def test_list_all(self):
        reg = _in_memory_registry()
        reg.upsert(_base_payload(factory_id="f1"), source="test")
        reg.upsert(_base_payload(factory_id="f2"), source="test")
        reg.upsert(_base_payload(factory_id="f3"), source="test")
        results = reg.list_all()
        assert len(results) == 3

    def test_list_with_status_filter(self):
        reg = _in_memory_registry()
        reg.upsert(_base_payload(factory_id="f1", registration_status="online"), source="test")
        reg.upsert(_base_payload(factory_id="f2", registration_status="stale"), source="test")
        reg.upsert(_base_payload(factory_id="f3", registration_status="offline"), source="test")
        online = reg.list_all(status_filter="online")
        assert len(online) == 1
        assert online[0]["factory_id"] == "f1"

    def test_list_respects_limit(self):
        reg = _in_memory_registry()
        for i in range(10):
            reg.upsert(_base_payload(factory_id=f"f{i}"), source="test")
        results = reg.list_all(limit=3)
        assert len(results) == 3

    def test_count(self):
        reg = _in_memory_registry()
        assert reg.count() == 0
        reg.upsert(_base_payload(factory_id="f1"), source="test")
        reg.upsert(_base_payload(factory_id="f2"), source="test")
        assert reg.count() == 2

    def test_delete_ids(self):
        reg = _in_memory_registry()
        reg.upsert(_base_payload(factory_id="f1"), source="test")
        reg.upsert(_base_payload(factory_id="f2"), source="test")
        reg.upsert(_base_payload(factory_id="f3"), source="test")
        deleted = reg.delete_ids(["f1", "f3", "missing"])
        assert deleted == 2
        remaining = [row["factory_id"] for row in reg.list_all(limit=10)]
        assert remaining == ["f2"]

    def test_list_returns_parsed_json(self):
        reg = _in_memory_registry()
        reg.upsert(
            _base_payload(
                factory_id="f1",
                capabilities=["a", "b"],
                metadata={"key": "val"},
            ),
            source="test",
        )
        result = reg.list_all()[0]
        assert isinstance(result["capabilities"], list)
        assert isinstance(result["metadata"], dict)
        assert result["capabilities"] == ["a", "b"]
        assert result["metadata"]["key"] == "val"


# ---------------------------------------------------------------------------
# Staleness Enforcement
# ---------------------------------------------------------------------------

class TestStalenessEnforcement:
    def _insert_with_last_seen(self, reg: FactoryRegistry, factory_id: str, seconds_ago: int):
        """Insert a factory and manually backdate its last_seen_at."""
        now = datetime.now(timezone.utc)
        past = (now - timedelta(seconds=seconds_ago)).isoformat()
        reg.upsert(_base_payload(factory_id=factory_id), source="test")
        # Manually update last_seen_at to simulate time passing
        reg._conn.execute(
            "UPDATE factory_registrations SET last_seen_at = ? WHERE factory_id = ?",
            (past, factory_id),
        )
        reg._conn.commit()

    def test_fresh_factory_stays_online(self):
        reg = _in_memory_registry()
        reg.upsert(_base_payload(factory_id="fresh"), source="test")
        result = reg.enforce_staleness()
        assert result["stale"] == 0
        assert result["offline"] == 0
        record = reg.get("fresh")
        assert record["registration_status"] == "online"

    def test_stale_factory_marked(self):
        reg = _in_memory_registry()
        self._insert_with_last_seen(reg, "old-factory", STALE_THRESHOLD_SECONDS + 60)
        result = reg.enforce_staleness()
        assert result["stale"] == 1
        record = reg.get("old-factory")
        assert record["registration_status"] == "stale"

    def test_offline_factory_marked(self):
        reg = _in_memory_registry()
        self._insert_with_last_seen(reg, "dead-factory", OFFLINE_THRESHOLD_SECONDS + 60)
        result = reg.enforce_staleness()
        assert result["offline"] == 1
        record = reg.get("dead-factory")
        assert record["registration_status"] == "offline"

    def test_heartbeat_revives_stale_factory(self):
        reg = _in_memory_registry()
        self._insert_with_last_seen(reg, "revived", STALE_THRESHOLD_SECONDS + 60)
        reg.enforce_staleness()
        assert reg.get("revived")["registration_status"] == "stale"

        # New heartbeat upsert should set status back to online
        reg.upsert(_base_payload(factory_id="revived", registration_status="online"), source="heartbeat")
        assert reg.get("revived")["registration_status"] == "online"

    def test_mixed_staleness(self):
        reg = _in_memory_registry()
        reg.upsert(_base_payload(factory_id="fresh"), source="test")
        self._insert_with_last_seen(reg, "stale-one", STALE_THRESHOLD_SECONDS + 60)
        self._insert_with_last_seen(reg, "dead-one", OFFLINE_THRESHOLD_SECONDS + 60)

        result = reg.enforce_staleness()
        assert result["stale"] == 1
        assert result["offline"] == 1
        assert reg.get("fresh")["registration_status"] == "online"
        assert reg.get("stale-one")["registration_status"] == "stale"
        assert reg.get("dead-one")["registration_status"] == "offline"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_survives_reconnect(self, tmp_path):
        db_path = str(tmp_path / "test_registry.db")

        # First connection: insert a factory
        conn1 = sqlite3.connect(db_path, check_same_thread=False)
        conn1.row_factory = sqlite3.Row
        reg1 = FactoryRegistry(conn1)
        reg1.upsert(_base_payload(factory_id="persistent"), source="test")
        conn1.close()

        # Second connection: verify data survives
        conn2 = sqlite3.connect(db_path, check_same_thread=False)
        conn2.row_factory = sqlite3.Row
        reg2 = FactoryRegistry(conn2)
        result = reg2.get("persistent")
        assert result is not None
        assert result["factory_id"] == "persistent"
        assert result["factory_role"] == "LOCAL_WORKER"
        conn2.close()
