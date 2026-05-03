"""Phase 3.5 — Chief-of-Staff cascade + cadence gate.

Phase 3 made the COS readout card-aware (Phase 3 tests cover that).
Phase 3.5 wires the AUTO-CASCADE: tier-2 fires on tier-0 transitions
or tier-1 success, with floor (2 min) / ceiling (5 min) cadence.

These tests cover:
  - _tier2_cascade_reason: precedence (tier-0 transitions first, then
    tier-1 success, then empty-string)
  - _tier2_skip_reason: 5-case matrix (first-run, floor, ceiling,
    in-window-no-cascade, in-window-with-cascade)
  - _run_tier2_async writes meta-row in all paths (success, skip,
    exception)
  - cascade fires real Chief-of-Staff service via mock
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from universal_agent.services.mission_control_db import open_store
from universal_agent.services.mission_control_intelligence_sweeper import (
    MissionControlSweeper,
    SweepResult,
    SweeperConfig,
)


# ── _tier2_cascade_reason: precedence ──────────────────────────────────


def _result(**kw) -> SweepResult:
    base = {
        "started_at_utc": "2026-05-03T16:00:00+00:00",
        "finished_at_utc": "2026-05-03T16:00:00+00:00",
    }
    base.update(kw)
    return SweepResult(**base)


def test_cascade_tier0_transitions_take_precedence():
    r = _result(tier0_transitions=["gateway:green->yellow"], tier1_synthesized=True)
    reason = MissionControlSweeper._tier2_cascade_reason(r)
    assert reason.startswith("tier0_transitions:")
    assert reason == "tier0_transitions:1"


def test_cascade_tier1_success_when_no_transitions():
    r = _result(tier0_transitions=[], tier1_synthesized=True)
    assert MissionControlSweeper._tier2_cascade_reason(r) == "tier1_synthesized"


def test_cascade_empty_when_nothing_changed():
    r = _result(tier0_transitions=[], tier1_synthesized=False)
    assert MissionControlSweeper._tier2_cascade_reason(r) == ""


# ── _tier2_skip_reason: 5-case matrix ──────────────────────────────────


def test_skip_first_run_always_runs():
    """No prior_synth_iso (this is the first tier-2 attempt) → always run."""
    reason = MissionControlSweeper._tier2_skip_reason(
        cascade_reason="",  # no cascade either
        prior_synth_iso=None,
        floor_seconds=120,
        ceiling_seconds=300,
    )
    assert reason is None  # run


def test_skip_within_floor_protects_lane():
    """Inside the floor window, skip even with cascade signal."""
    recent = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    reason = MissionControlSweeper._tier2_skip_reason(
        cascade_reason="tier1_synthesized",
        prior_synth_iso=recent,
        floor_seconds=120,
        ceiling_seconds=300,
    )
    assert reason and "floor_protection" in reason


def test_skip_past_ceiling_always_runs():
    """Past the ceiling, always refresh regardless of cascade — keeps
    the readout from staling on idle systems."""
    old = (datetime.now(timezone.utc) - timedelta(seconds=500)).isoformat()
    reason = MissionControlSweeper._tier2_skip_reason(
        cascade_reason="",
        prior_synth_iso=old,
        floor_seconds=120,
        ceiling_seconds=300,
    )
    assert reason is None


def test_skip_in_window_no_cascade_skips():
    """Between floor and ceiling, skip if no cascade trigger fired."""
    mid = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    reason = MissionControlSweeper._tier2_skip_reason(
        cascade_reason="",
        prior_synth_iso=mid,
        floor_seconds=120,
        ceiling_seconds=300,
    )
    assert reason and "no_cascade_signal" in reason


def test_skip_in_window_with_cascade_runs():
    """Between floor and ceiling, RUN if cascade trigger fired."""
    mid = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    reason = MissionControlSweeper._tier2_skip_reason(
        cascade_reason="tier1_synthesized",
        prior_synth_iso=mid,
        floor_seconds=120,
        ceiling_seconds=300,
    )
    assert reason is None


# ── _run_tier2_async end-to-end with mocked COS ────────────────────────


@pytest.mark.asyncio
async def test_tier2_async_writes_meta_on_success(tmp_path: Path, monkeypatch):
    """When the cascade fires and COS succeeds, _run_tier2_async should
    flip tier2_synthesized=True and write a __tier2_meta__ row."""
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(tmp_path / "mc.db"))

    async def _fake_cos():
        return {"headline": "All systems nominal", "model": "glm-5.1"}

    from universal_agent.services import mission_control_chief_of_staff as cos_mod
    monkeypatch.setattr(cos_mod, "generate_and_store_readout", _fake_cos)

    sweeper = MissionControlSweeper(SweeperConfig())
    result = _result(tier1_synthesized=True)  # cascade trigger
    await sweeper._run_tier2_async(result)
    assert result.tier2_synthesized is True

    conn = open_store(tmp_path / "mc.db")
    try:
        row = conn.execute(
            "SELECT current_annotation, evidence_payload_json FROM mission_control_tile_states "
            "WHERE tile_id = ?",
            ("__tier2_meta__",),
        ).fetchone()
        assert row is not None
        assert "tier2 ok" in row["current_annotation"]
        assert "All systems nominal" in row["current_annotation"]
        payload = json.loads(row["evidence_payload_json"])
        assert payload["model"] == "glm-5.1"
        assert payload["cascade_reason"] == "tier1_synthesized"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_tier2_async_writes_meta_on_skip(tmp_path: Path, monkeypatch):
    """When tier-2 is skipped (e.g. floor protection), still write a
    meta-row so /diagnostics surfaces the reason."""
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(tmp_path / "mc.db"))

    # Pre-populate a recent prior tier-2 row to trigger floor-protection
    conn = open_store(tmp_path / "mc.db")
    try:
        conn.execute(
            """
            INSERT INTO mission_control_tile_states (
                tile_id, current_state, state_since, last_signature,
                last_checked_at, current_annotation
            ) VALUES (?, 'unknown', ?, '', ?, 'prior')
            """,
            (
                "__tier2_meta__",
                datetime.now(timezone.utc).isoformat(),  # very recent
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    finally:
        conn.close()

    cos_called = {"n": 0}
    async def _fake_cos():
        cos_called["n"] += 1
        return {"headline": "should not be called", "model": "?"}

    from universal_agent.services import mission_control_chief_of_staff as cos_mod
    monkeypatch.setattr(cos_mod, "generate_and_store_readout", _fake_cos)

    sweeper = MissionControlSweeper(SweeperConfig())
    result = _result(tier1_synthesized=True)
    await sweeper._run_tier2_async(result)
    assert result.tier2_synthesized is False
    assert cos_called["n"] == 0  # COS should NOT have been invoked

    conn = open_store(tmp_path / "mc.db")
    try:
        row = conn.execute(
            "SELECT current_annotation FROM mission_control_tile_states WHERE tile_id = ?",
            ("__tier2_meta__",),
        ).fetchone()
        assert "tier2 skipped: floor_protection" in row["current_annotation"]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_tier2_async_writes_meta_on_exception(tmp_path: Path, monkeypatch):
    """When COS raises, the meta-row should capture the error and the
    sweeper loop should continue (errors land in result.errors)."""
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(tmp_path / "mc.db"))

    async def _broken_cos():
        raise RuntimeError("synthetic COS failure")

    from universal_agent.services import mission_control_chief_of_staff as cos_mod
    monkeypatch.setattr(cos_mod, "generate_and_store_readout", _broken_cos)

    sweeper = MissionControlSweeper(SweeperConfig())
    result = _result(tier0_transitions=["x:green->red"])
    await sweeper._run_tier2_async(result)
    assert result.tier2_synthesized is False
    assert any("synthetic COS failure" in e for e in result.errors)

    conn = open_store(tmp_path / "mc.db")
    try:
        row = conn.execute(
            "SELECT current_annotation FROM mission_control_tile_states WHERE tile_id = ?",
            ("__tier2_meta__",),
        ).fetchone()
        assert "tier2 COS raised" in row["current_annotation"]
        assert "synthetic COS failure" in row["current_annotation"]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_tier2_skips_when_no_cascade_in_window(tmp_path: Path, monkeypatch):
    """Mid-window with no cascade trigger → skip with no_cascade_signal
    annotation, COS not invoked."""
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(tmp_path / "mc.db"))

    # Prior row 200s ago (between 120s floor and 300s ceiling)
    mid = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    conn = open_store(tmp_path / "mc.db")
    try:
        conn.execute(
            """
            INSERT INTO mission_control_tile_states (
                tile_id, current_state, state_since, last_signature,
                last_checked_at, current_annotation
            ) VALUES (?, 'unknown', ?, '', ?, 'prior')
            """,
            ("__tier2_meta__", mid, mid),
        )
    finally:
        conn.close()

    cos_called = {"n": 0}
    async def _fake_cos():
        cos_called["n"] += 1
        return {"headline": "x", "model": "y"}

    from universal_agent.services import mission_control_chief_of_staff as cos_mod
    monkeypatch.setattr(cos_mod, "generate_and_store_readout", _fake_cos)

    sweeper = MissionControlSweeper(SweeperConfig(
        tier2_floor_seconds=120, tier2_ceiling_seconds=300,
    ))
    # No cascade signal: no transitions, no tier-1 synth
    result = _result(tier0_transitions=[], tier1_synthesized=False)
    await sweeper._run_tier2_async(result)
    assert cos_called["n"] == 0
    conn = open_store(tmp_path / "mc.db")
    try:
        row = conn.execute(
            "SELECT current_annotation FROM mission_control_tile_states WHERE tile_id = ?",
            ("__tier2_meta__",),
        ).fetchone()
        assert "no_cascade_signal" in row["current_annotation"]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_run_async_tiers_invokes_tier2_after_tier1(tmp_path: Path, monkeypatch):
    """Integration: run_async_tiers should call _run_tier1_async and
    _run_tier2_async in order on the same sweep."""
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(tmp_path / "mc.db"))
    monkeypatch.setenv("UA_MC_PHASE_2_ENABLED", "1")

    sequence: list[str] = []

    async def _fake_tier1(self_, result):
        sequence.append("tier1")
        result.tier1_synthesized = True

    async def _fake_tier2(self_, result):
        sequence.append("tier2")
        result.tier2_synthesized = True

    monkeypatch.setattr(MissionControlSweeper, "_run_tier1_async", _fake_tier1)
    monkeypatch.setattr(MissionControlSweeper, "_run_tier2_async", _fake_tier2)

    sweeper = MissionControlSweeper(SweeperConfig())
    result = _result()
    await sweeper.run_async_tiers(result)
    assert sequence == ["tier1", "tier2"]
