"""Unit tests for the mission_control_sweeper_liveness invariant (Phase B follow-up).

Locks the false-alarm guards: WARN only when phase 1 is enabled AND the per-tick
heartbeat (last_checked_at) is genuinely stale; no-op when the sweeper is
intentionally off, the DB/row is absent, or the heartbeat is fresh.

Dual-signal tests (2026-06-13): tier-1 staleness alone does not WARN when
tier-0 tiles are fresh (the sweeper loop is alive, tier-1 legitimately idle).
Only when BOTH are stale does the invariant fire.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from universal_agent.services.invariants.mission_control_sweeper_liveness import (
    _max_stale_seconds,
    mission_control_sweeper_liveness,
)
from universal_agent.services.mission_control_db import open_store


def _seed(db_path: Path, last_checked: datetime | None) -> None:
    conn = open_store(db_path)  # creates the real schema
    if last_checked is not None:
        conn.execute(
            "INSERT OR REPLACE INTO mission_control_tile_states "
            "(tile_id, current_state, state_since, last_checked_at) "
            "VALUES (?, ?, ?, ?)",
            (
                "__tier1_meta__",
                "unknown",
                last_checked.isoformat(),
                last_checked.isoformat(),
            ),
        )
    conn.close()


def _seed_tile(db_path: Path, tile_id: str, last_checked: datetime) -> None:
    """Seed a tier-0 tile row with the given tile_id and last_checked_at."""
    conn = open_store(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO mission_control_tile_states "
        "(tile_id, current_state, state_since, last_checked_at) "
        "VALUES (?, ?, ?, ?)",
        (
            tile_id,
            "green",
            last_checked.isoformat(),
            last_checked.isoformat(),
        ),
    )
    conn.close()


@pytest.fixture
def mc_db(tmp_path, monkeypatch) -> Path:
    db = tmp_path / "mission_control_intelligence.db"
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(db))
    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")  # sweeper active
    return db


def test_disabled_phase_is_noop(tmp_path, monkeypatch):
    db = tmp_path / "x.db"
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(db))
    monkeypatch.delenv("UA_MC_PHASE_1_ENABLED", raising=False)  # phase OFF
    # Even a 2h-stale heartbeat must not alert when the sweeper is parked.
    _seed(db, datetime.now(timezone.utc) - timedelta(hours=2))
    assert mission_control_sweeper_liveness({}) is None


def test_missing_db_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(tmp_path / "nope.db"))
    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")
    assert mission_control_sweeper_liveness({}) is None


def test_no_meta_row_is_noop(mc_db):
    _seed(mc_db, None)  # schema exists, no __tier1_meta__ row
    assert mission_control_sweeper_liveness({}) is None


def test_fresh_heartbeat_healthy(mc_db):
    _seed(mc_db, datetime.now(timezone.utc) - timedelta(seconds=30))
    assert mission_control_sweeper_liveness({}) is None


def test_stale_heartbeat_warns(mc_db):
    _seed(mc_db, datetime.now(timezone.utc) - timedelta(seconds=600))
    _seed_tile(mc_db, "task_hub_pressure", datetime.now(timezone.utc) - timedelta(seconds=600))
    result = mission_control_sweeper_liveness({})
    assert result is not None
    assert result["observed_value"]["stale_seconds"] >= 590
    assert result["observed_value"]["max_stale_seconds"] == 300
    assert "wedged" in result["message"]


def test_threshold_env_override_makes_borderline_stale(mc_db, monkeypatch):
    monkeypatch.setenv("UA_MC_SWEEPER_LIVENESS_MAX_STALE_SECONDS", "120")
    assert _max_stale_seconds() == 120
    # 200s is healthy at the 300s default but stale under the 120s override.
    _seed(mc_db, datetime.now(timezone.utc) - timedelta(seconds=200))
    _seed_tile(mc_db, "task_hub_pressure", datetime.now(timezone.utc) - timedelta(seconds=200))
    result = mission_control_sweeper_liveness({})
    assert result is not None
    assert result["observed_value"]["max_stale_seconds"] == 120


def test_max_stale_seconds_floor_and_default(monkeypatch):
    monkeypatch.delenv("UA_MC_SWEEPER_LIVENESS_MAX_STALE_SECONDS", raising=False)
    assert _max_stale_seconds() == 300
    monkeypatch.setenv("UA_MC_SWEEPER_LIVENESS_MAX_STALE_SECONDS", "5")  # below floor
    assert _max_stale_seconds() == 120
    monkeypatch.setenv("UA_MC_SWEEPER_LIVENESS_MAX_STALE_SECONDS", "not-an-int")
    assert _max_stale_seconds() == 300


# ── Dual-signal tests (2026-06-13) ──────────────────────────────────────


def test_tier1_stale_but_tier0_fresh_is_healthy(mc_db):
    """Core false-positive fix: tier-1 meta stale but tier-0 tiles fresh => healthy."""
    _seed(mc_db, datetime.now(timezone.utc) - timedelta(seconds=600))
    _seed_tile(mc_db, "task_hub_pressure", datetime.now(timezone.utc) - timedelta(seconds=30))
    assert mission_control_sweeper_liveness({}) is None


def test_tier1_stale_and_tier0_stale_warns(mc_db):
    """Both tier-1 meta AND tier-0 tiles stale => still warns (genuinely wedged)."""
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=600)
    _seed(mc_db, stale_time)
    _seed_tile(mc_db, "task_hub_pressure", stale_time)
    result = mission_control_sweeper_liveness({})
    assert result is not None
    assert "Both tier-0 tiles AND tier-1 meta are stale" in result["message"]
    assert result["observed_value"]["stale_seconds"] >= 590


def test_tier1_stale_no_tier0_rows_is_noop(mc_db):
    """Tier-1 stale but no tier-0 tile rows at all => fails open (no data to confirm death)."""
    _seed(mc_db, datetime.now(timezone.utc) - timedelta(seconds=600))
    # No tier-0 tile rows seeded
    assert mission_control_sweeper_liveness({}) is None
