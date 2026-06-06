"""S3 — Unfreeze the Chief-of-Staff readout: fire-time vs attempt-time.

Regression coverage for the sticky-bit-on-skip bug that froze the tier-2
(Chief-of-Staff) readout. `_write_tier2_meta` / `_write_tier1_meta` used to
stamp `state_since = now` on EVERY write — including skips and errors. Since
`_run_tierN_async` reads `state_since` back as `prior_synth_iso` and
`_tierN_skip_reason` uses it as the "age since last fire" clock, advancing it
on every 60s sweep reset the clock to ~0, the `age_s >= ceiling_seconds`
idle-refresh branch could never trigger, and the readout never refreshed on an
idle system.

The fix makes `state_since` mean "last actual FIRE": only the real-fire path
passes ``advance_fire_ts=True``; skip/error paths pass ``False`` and preserve
the prior `state_since` while still refreshing diagnostic columns.

These tests prove:
  (a) a sequence of skip writes does NOT advance `state_since`;
  (b) after `ceiling_seconds` of wall-time-since-last-fire with no cascade,
      `_tier2_skip_reason` returns None (it fires) AND `_run_tier2_async`
      actually synthesizes — i.e. the idle ceiling is reachable end-to-end;
  (c) an actual fire advances `state_since`;
  (d) tier-1 behaves consistently (skip preserves `state_since`, but
      `last_signature` is still updated on skip — signature tracking is
      independent of fire-time);
  (e) first-ever write seeds `state_since` even on a skip.

CI note: monkeypatch by OBJECT (module attr), never string import-path —
`derive_importpath` traversal is fragile under the full suite's sys.modules.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from universal_agent.services.mission_control_db import open_store
from universal_agent.services.mission_control_intelligence_sweeper import (
    MissionControlSweeper,
    SweeperConfig,
    SweepResult,
)


def _result(**kw) -> SweepResult:
    base = {
        "started_at_utc": "2026-06-05T00:00:00+00:00",
        "finished_at_utc": "2026-06-05T00:00:00+00:00",
    }
    base.update(kw)
    return SweepResult(**base)


def _iso_ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _parse(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _seed_meta(conn, tile_id: str, *, state_since: str, last_signature: str = "") -> None:
    conn.execute(
        """
        INSERT INTO mission_control_tile_states (
            tile_id, current_state, state_since, last_signature,
            last_checked_at, current_annotation
        ) VALUES (?, 'unknown', ?, ?, ?, 'prior fire')
        """,
        (tile_id, state_since, last_signature, state_since),
    )


def _read_meta(conn, tile_id: str) -> dict:
    row = conn.execute(
        "SELECT state_since, last_signature, last_checked_at, current_annotation "
        "FROM mission_control_tile_states WHERE tile_id = ?",
        (tile_id,),
    ).fetchone()
    return dict(row) if row is not None else None


# ── (a) + (c): tier-2 writer skip preserves, fire advances ─────────────


def test_tier2_meta_skip_preserves_state_since_and_fire_advances(tmp_path: Path):
    conn = open_store(tmp_path / "mc.db")
    try:
        t0 = _iso_ago(400)  # "last fired 400s ago" — well past the 300s ceiling
        _seed_meta(conn, "__tier2_meta__", state_since=t0)

        # Three consecutive SKIP writes must NOT move state_since.
        for i in range(3):
            MissionControlSweeper._write_tier2_meta(
                conn,
                annotation=f"tier2 skipped: floor_protection (iter={i})",
                payload={"cascade_reason": ""},
                advance_fire_ts=False,
            )
            row = _read_meta(conn, "__tier2_meta__")
            assert row["state_since"] == t0, f"skip #{i} advanced state_since"
            # ...but diagnostic columns DO refresh so /diagnostics stays live.
            assert "skipped" in row["current_annotation"]
            assert _parse(row["last_checked_at"]) > _parse(t0)

        # The preserved clock means the ceiling is now reachable.
        assert (
            MissionControlSweeper._tier2_skip_reason(
                cascade_reason="",
                prior_synth_iso=row["state_since"],
                floor_seconds=120,
                ceiling_seconds=300,
            )
            is None
        )

        # A real FIRE advances state_since to ~now.
        MissionControlSweeper._write_tier2_meta(
            conn,
            annotation="tier2 ok: cascade=ceiling",
            payload={"cascade_reason": "ceiling"},
            advance_fire_ts=True,
        )
        row = _read_meta(conn, "__tier2_meta__")
        age_after_fire = (datetime.now(timezone.utc) - _parse(row["state_since"])).total_seconds()
        assert age_after_fire < 30, "fire did not advance state_since to ~now"
        assert "tier2 ok" in row["current_annotation"]
    finally:
        conn.close()


# ── (e): first-ever write seeds state_since even on a skip ─────────────


def test_tier2_meta_first_write_seeds_state_since_on_skip(tmp_path: Path):
    conn = open_store(tmp_path / "mc.db")
    try:
        assert _read_meta(conn, "__tier2_meta__") is None  # no prior row
        MissionControlSweeper._write_tier2_meta(
            conn,
            annotation="tier2 skipped: floor_protection",
            payload=None,
            advance_fire_ts=False,
        )
        row = _read_meta(conn, "__tier2_meta__")
        assert row is not None
        assert row["state_since"], "first write must seed a non-empty state_since"
        age = (datetime.now(timezone.utc) - _parse(row["state_since"])).total_seconds()
        assert age < 30, "first-write state_since should be ~now"
    finally:
        conn.close()


# ── (d): tier-1 writer — skip preserves state_since, updates signature ──


def test_tier1_meta_skip_preserves_state_since_but_updates_signature(tmp_path: Path):
    conn = open_store(tmp_path / "mc.db")
    try:
        t0 = _iso_ago(400)
        _seed_meta(conn, "__tier1_meta__", state_since=t0, last_signature="sigOLD")

        # Skip write: state_since frozen, but last_signature must advance
        # (signature tracking is independent of fire-time).
        MissionControlSweeper._write_tier1_meta(
            conn,
            signature="sigNEW",
            annotation="tier1 skipped: signature_changed_but_rate_limited",
            payload=None,
            advance_fire_ts=False,
        )
        row = _read_meta(conn, "__tier1_meta__")
        assert row["state_since"] == t0, "tier-1 skip advanced state_since"
        assert row["last_signature"] == "sigNEW", "tier-1 skip must still record new signature"

        # Fire write: state_since advances.
        MissionControlSweeper._write_tier1_meta(
            conn,
            signature="sigNEWER",
            annotation="tier1 ok: created/updated=2",
            payload={"model": "glm-5.1"},
            advance_fire_ts=True,
        )
        row = _read_meta(conn, "__tier1_meta__")
        age = (datetime.now(timezone.utc) - _parse(row["state_since"])).total_seconds()
        assert age < 30, "tier-1 fire did not advance state_since"
        assert row["last_signature"] == "sigNEWER"
    finally:
        conn.close()


# ── (b): end-to-end — idle system fires at the ceiling ─────────────────


@pytest.mark.asyncio
async def test_idle_system_fires_at_ceiling_via_run_tier2_async(tmp_path: Path, monkeypatch):
    """With NO cascade signal and last fire > ceiling ago, _run_tier2_async
    must synthesize (idle-refresh ceiling reachable) and advance state_since."""
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(tmp_path / "mc.db"))

    conn = open_store(tmp_path / "mc.db")
    try:
        _seed_meta(conn, "__tier2_meta__", state_since=_iso_ago(360))  # comfortably past 300s ceiling
    finally:
        conn.close()

    calls = {"n": 0}

    async def _fake_cos():
        calls["n"] += 1
        return {"headline": "idle refresh", "model": "glm-5.1"}

    from universal_agent.services import mission_control_chief_of_staff as cos_mod
    monkeypatch.setattr(cos_mod, "generate_and_store_readout", _fake_cos)

    sweeper = MissionControlSweeper(
        SweeperConfig(tier2_floor_seconds=120, tier2_ceiling_seconds=300)
    )
    result = _result(tier0_transitions=[], tier1_synthesized=False)  # NO cascade
    await sweeper._run_tier2_async(result)

    assert calls["n"] == 1, "ceiling did not fire on an idle system"
    assert result.tier2_synthesized is True

    conn = open_store(tmp_path / "mc.db")
    try:
        row = _read_meta(conn, "__tier2_meta__")
        assert "tier2 ok" in row["current_annotation"]
        age = (datetime.now(timezone.utc) - _parse(row["state_since"])).total_seconds()
        assert age < 30, "fire did not advance state_since"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_idle_skip_does_not_reset_clock_via_run_tier2_async(tmp_path: Path, monkeypatch):
    """The bug's core: a no-cascade SKIP inside the floor..ceiling window must
    NOT reset state_since to now. Under the old writer it did, so the clock
    never climbed to the ceiling and the readout froze."""
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(tmp_path / "mc.db"))

    conn = open_store(tmp_path / "mc.db")
    try:
        _seed_meta(conn, "__tier2_meta__", state_since=_iso_ago(200))  # in window
    finally:
        conn.close()

    calls = {"n": 0}

    async def _fake_cos():
        calls["n"] += 1
        return {"headline": "should not fire", "model": "?"}

    from universal_agent.services import mission_control_chief_of_staff as cos_mod
    monkeypatch.setattr(cos_mod, "generate_and_store_readout", _fake_cos)

    sweeper = MissionControlSweeper(
        SweeperConfig(tier2_floor_seconds=120, tier2_ceiling_seconds=300)
    )
    result = _result(tier0_transitions=[], tier1_synthesized=False)  # NO cascade
    await sweeper._run_tier2_async(result)

    assert calls["n"] == 0, "should have skipped (no cascade, in window)"
    conn = open_store(tmp_path / "mc.db")
    try:
        row = _read_meta(conn, "__tier2_meta__")
        assert "no_cascade_signal" in row["current_annotation"]
        # The clock kept climbing — state_since still reflects ~200s ago, NOT now.
        age = (datetime.now(timezone.utc) - _parse(row["state_since"])).total_seconds()
        assert age > 100, "skip reset the clock (the frozen-readout bug)"
    finally:
        conn.close()


# ── tier-1 end-to-end: real _run_tier1_async skip preserves the clock ──


@pytest.mark.asyncio
async def test_idle_skip_does_not_reset_clock_via_run_tier1_async(tmp_path: Path, monkeypatch):
    """Tier-1 mirror of the tier-2 regression, exercised through the REAL
    _run_tier1_async skip branch (not just the writer): a signature-unchanged
    skip inside the ceiling window must NOT reset state_since. Closes the
    coverage gap where existing tier-1 async tests monkeypatch the whole
    _run_tier1_async method and never touch the skip→writer wiring."""
    import sqlite3

    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(tmp_path / "mc.db"))

    conn = open_store(tmp_path / "mc.db")
    try:
        _seed_meta(conn, "__tier1_meta__", state_since=_iso_ago(200), last_signature="SIG")
    finally:
        conn.close()

    from universal_agent.services import mission_control_tier1 as t1_mod
    # Accept **_kw so the sweeper's `activity_read_only=True` kwarg (the
    # ro activity-handle hardening) doesn't trip this stub.
    monkeypatch.setattr(t1_mod, "collect_tier1_evidence", lambda data_conn, mc_conn, **_kw: {"counts": {}})
    monkeypatch.setattr(t1_mod, "evidence_signature", lambda evidence: "SIG")  # unchanged signature

    discover_called = {"n": 0}

    async def _no_discover(evidence):
        discover_called["n"] += 1
        return ([], "model")

    monkeypatch.setattr(t1_mod, "discover_tier1_cards", _no_discover)
    monkeypatch.setattr(
        MissionControlSweeper, "_open_activity_db", lambda self: sqlite3.connect(":memory:")
    )

    # Unchanged signature within the (large) ceiling → skip "signature_unchanged".
    sweeper = MissionControlSweeper(
        SweeperConfig(tier1_floor_seconds=180, tier1_ceiling_seconds=1800)
    )
    result = _result()
    await sweeper._run_tier1_async(result)

    assert discover_called["n"] == 0, "tier-1 should have skipped (signature unchanged in ceiling)"
    assert result.tier1_synthesized is False

    conn = open_store(tmp_path / "mc.db")
    try:
        row = _read_meta(conn, "__tier1_meta__")
        assert "signature_unchanged" in row["current_annotation"]
        age = (datetime.now(timezone.utc) - _parse(row["state_since"])).total_seconds()
        assert age > 100, "tier-1 skip reset the clock (the dead-ceiling defect)"
        assert row["last_signature"] == "SIG"
    finally:
        conn.close()
