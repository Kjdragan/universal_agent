"""Phase 0 foundations for the Mission Control Intelligence System.

Verifies the scaffolding shipped in Phase 0 — the model lane resolver,
the SQLite schema for cards/tiles/dispatch/templates, and the
feature-flag-gated sweeper skeleton — without depending on any later
phase being enabled. These tests must remain green even when every
`UA_MC_PHASE_<N>_ENABLED` flag is unset, which mirrors the production
default during Phase 0 rollout.

See docs/02_Subsystems/Mission_Control_Intelligence_System.md.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest


# ── Model resolver ────────────────────────────────────────────────────


def test_mission_control_model_defaults_to_glm_4_7(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default Mission Control lane = glm-4.7 — the dedicated direct model
    that bypasses the ZAI haiku/sonnet/opus tier collapse so its compute
    does not contend with the application's main agent calls.
    """
    from universal_agent.utils.model_resolution import (
        MISSION_CONTROL_DEFAULT_MODEL,
        resolve_mission_control_model,
    )

    monkeypatch.delenv("UA_MISSION_CONTROL_MODEL", raising=False)
    assert MISSION_CONTROL_DEFAULT_MODEL == "glm-4.7"
    assert resolve_mission_control_model() == "glm-4.7"


def test_mission_control_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from universal_agent.utils.model_resolution import resolve_mission_control_model

    monkeypatch.setenv("UA_MISSION_CONTROL_MODEL", "glm-5-turbo")
    assert resolve_mission_control_model() == "glm-5-turbo"


def test_mission_control_model_does_not_pass_through_zai_map(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting a tier-style env override (haiku/sonnet/opus) must NOT
    influence the Mission Control lane. The whole point of the dedicated
    designation is independence from those mappings.
    """
    from universal_agent.utils.model_resolution import resolve_mission_control_model

    monkeypatch.delenv("UA_MISSION_CONTROL_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4")
    assert resolve_mission_control_model() == "glm-4.7"


def test_mission_control_call_timeout_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from universal_agent.utils.model_resolution import mission_control_call_timeout_seconds

    monkeypatch.delenv("UA_MISSION_CONTROL_CALL_TIMEOUT_SECONDS", raising=False)
    assert mission_control_call_timeout_seconds() == 180.0


def test_mission_control_call_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from universal_agent.utils.model_resolution import mission_control_call_timeout_seconds

    monkeypatch.setenv("UA_MISSION_CONTROL_CALL_TIMEOUT_SECONDS", "45")
    assert mission_control_call_timeout_seconds() == 45.0


# ── SQLite schema ─────────────────────────────────────────────────────


def _open_test_store(tmp_path: Path):
    from universal_agent.services.mission_control_db import open_store

    db_path = tmp_path / "mc_test.db"
    return open_store(db_path), db_path


def test_schema_creates_all_four_tables(tmp_path: Path) -> None:
    conn, _ = _open_test_store(tmp_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {row["name"] for row in rows}
        for required in {
            "mission_control_cards",
            "mission_control_tile_states",
            "mission_control_dispatch_history",
            "event_title_templates",
        }:
            assert required in names, f"missing table: {required}"
    finally:
        conn.close()


def test_schema_is_idempotent(tmp_path: Path) -> None:
    """ensure_schema() must be safe to call multiple times — the sweeper
    will call it on every connection open."""
    from universal_agent.services.mission_control_db import ensure_schema, open_store

    db_path = tmp_path / "mc_idempotent.db"
    conn = open_store(db_path)
    try:
        ensure_schema(conn)
        ensure_schema(conn)
        ensure_schema(conn)
    finally:
        conn.close()


def test_card_subject_kind_constraint_rejects_bad_values(tmp_path: Path) -> None:
    """The seven valid subject kinds are enforced at the schema level so a
    buggy producer can't land an unknown subject_kind that would later
    confuse retire-timing logic."""
    conn, _ = _open_test_store(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO mission_control_cards
                  (card_id, subject_kind, subject_id, current_state, severity,
                   title, narrative, why_it_matters, first_observed_at, last_synthesized_at)
                VALUES
                  ('c1','bogus_kind','sid','live','warning',
                   't','n','w','2026-05-03T00:00:00Z','2026-05-03T00:00:00Z')
                """
            )
    finally:
        conn.close()


def test_card_severity_constraint_rejects_bad_values(tmp_path: Path) -> None:
    conn, _ = _open_test_store(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO mission_control_cards
                  (card_id, subject_kind, subject_id, current_state, severity,
                   title, narrative, why_it_matters, first_observed_at, last_synthesized_at)
                VALUES
                  ('c2','task','t1','live','meh',
                   't','n','w','2026-05-03T00:00:00Z','2026-05-03T00:00:00Z')
                """
            )
    finally:
        conn.close()


def test_card_uniqueness_on_subject(tmp_path: Path) -> None:
    """Stable identity: only one live row per (subject_kind, subject_id)."""
    conn, _ = _open_test_store(tmp_path)
    try:
        conn.execute(
            """
            INSERT INTO mission_control_cards
              (card_id, subject_kind, subject_id, current_state, severity,
               title, narrative, why_it_matters, first_observed_at, last_synthesized_at)
            VALUES
              ('c3','task','t1','live','warning',
               't','n','w','2026-05-03T00:00:00Z','2026-05-03T00:00:00Z')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO mission_control_cards
                  (card_id, subject_kind, subject_id, current_state, severity,
                   title, narrative, why_it_matters, first_observed_at, last_synthesized_at)
                VALUES
                  ('c3-dup','task','t1','live','warning',
                   't','n','w','2026-05-03T00:00:00Z','2026-05-03T00:00:00Z')
                """
            )
    finally:
        conn.close()


def test_card_no_truncation_on_long_text(tmp_path: Path) -> None:
    """Phase 0 contract: narrative / why_it_matters / next_step / payload
    must accept arbitrarily long text. We round-trip a 200KB blob through
    the schema and verify it survives unchanged.
    """
    conn, _ = _open_test_store(tmp_path)
    try:
        big = "Mission Control narrative content. " * 6000  # ~210KB
        assert len(big) > 100_000
        conn.execute(
            """
            INSERT INTO mission_control_cards
              (card_id, subject_kind, subject_id, current_state, severity,
               title, narrative, why_it_matters, recommended_next_step,
               evidence_payload_json,
               first_observed_at, last_synthesized_at)
            VALUES
              ('c-big','idea','idea:huge','live','informational',
               'big card', ?, ?, ?, ?,
               '2026-05-03T00:00:00Z','2026-05-03T00:00:00Z')
            """,
            (big, big, big, big),
        )
        row = conn.execute(
            "SELECT narrative, why_it_matters, recommended_next_step, evidence_payload_json "
            "FROM mission_control_cards WHERE card_id=?",
            ("c-big",),
        ).fetchone()
        assert len(row["narrative"]) == len(big)
        assert len(row["why_it_matters"]) == len(big)
        assert len(row["recommended_next_step"]) == len(big)
        assert len(row["evidence_payload_json"]) == len(big)
    finally:
        conn.close()


def test_dispatch_action_constraint(tmp_path: Path) -> None:
    conn, _ = _open_test_store(tmp_path)
    try:
        # First create the parent card so the FK doesn't reject us.
        conn.execute(
            """
            INSERT INTO mission_control_cards
              (card_id, subject_kind, subject_id, current_state, severity,
               title, narrative, why_it_matters, first_observed_at, last_synthesized_at)
            VALUES
              ('c-d','task','td','live','warning',
               't','n','w','2026-05-03T00:00:00Z','2026-05-03T00:00:00Z')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO mission_control_dispatch_history
                  (dispatch_id, card_id, action, ts, prompt_text)
                VALUES ('d1','c-d','sent_to_unknown','2026-05-03T00:00:00Z','...')
                """
            )
    finally:
        conn.close()


# ── Sweeper skeleton + phase-flag gating ──────────────────────────────


def test_phase0_always_enabled() -> None:
    from universal_agent.services.mission_control_db import is_phase_enabled

    assert is_phase_enabled(0) is True


def test_phase_flags_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from universal_agent.services.mission_control_db import is_phase_enabled

    for n in range(1, 9):
        monkeypatch.delenv(f"UA_MC_PHASE_{n}_ENABLED", raising=False)
        assert is_phase_enabled(n) is False, f"phase {n} should default to disabled"


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "on", "enabled"])
def test_phase_flag_truthy_values(monkeypatch: pytest.MonkeyPatch, truthy: str) -> None:
    from universal_agent.services.mission_control_db import is_phase_enabled

    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", truthy)
    assert is_phase_enabled(1) is True


def test_sweeper_tick_skips_when_phase1_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 0 contract: importing the sweeper and ticking it must not
    invoke any tier handler until phase 1 is enabled. Otherwise the
    foundation phase would silently start doing work in production.
    """
    from universal_agent.services.mission_control_intelligence_sweeper import (
        MissionControlSweeper,
        SweeperConfig,
    )

    for n in range(1, 9):
        monkeypatch.delenv(f"UA_MC_PHASE_{n}_ENABLED", raising=False)
    sweeper = MissionControlSweeper(SweeperConfig())
    result = sweeper.tick()
    assert result.skipped_reason == "phase_1_not_enabled"
    assert result.tier0_checked is False
    assert result.tier1_evaluated is False
    assert result.tier2_evaluated is False
    assert result.errors == []


def test_sweeper_tick_runs_tier0_only_when_phase1_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from universal_agent.services.mission_control_intelligence_sweeper import (
        MissionControlSweeper,
        SweeperConfig,
    )

    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")
    for n in range(2, 9):
        monkeypatch.delenv(f"UA_MC_PHASE_{n}_ENABLED", raising=False)
    sweeper = MissionControlSweeper(SweeperConfig())
    result = sweeper.tick()
    assert result.skipped_reason is None
    assert result.tier0_checked is True
    assert result.tier1_evaluated is False
    assert result.tier2_evaluated is False


def test_sweeper_tick_runs_tier1_when_phase2_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from universal_agent.services.mission_control_intelligence_sweeper import (
        MissionControlSweeper,
        SweeperConfig,
    )

    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")
    monkeypatch.setenv("UA_MC_PHASE_2_ENABLED", "1")
    monkeypatch.delenv("UA_MC_PHASE_3_ENABLED", raising=False)
    sweeper = MissionControlSweeper(SweeperConfig())
    result = sweeper.tick()
    assert result.tier0_checked is True
    assert result.tier1_evaluated is True
    assert result.tier2_evaluated is False


def test_sweeper_config_from_env_picks_up_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    from universal_agent.services.mission_control_intelligence_sweeper import SweeperConfig

    monkeypatch.setenv("UA_MISSION_CONTROL_SWEEPER_INTERVAL_S", "30")
    monkeypatch.setenv("UA_MISSION_CONTROL_LANE_CONCURRENCY", "2")
    monkeypatch.setenv("UA_MISSION_CONTROL_AUTO_REMEDIATION", "1")
    cfg = SweeperConfig.from_env()
    assert cfg.interval_seconds == 30.0
    assert cfg.lane_concurrency == 2
    assert cfg.auto_remediation_enabled is True


def test_sweeper_config_from_env_handles_garbage_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    from universal_agent.services.mission_control_intelligence_sweeper import SweeperConfig

    monkeypatch.setenv("UA_MISSION_CONTROL_SWEEPER_INTERVAL_S", "not-a-number")
    monkeypatch.setenv("UA_MISSION_CONTROL_LANE_CONCURRENCY", "bogus")
    cfg = SweeperConfig.from_env()
    # Garbage -> fall back to defaults rather than crash the daemon
    assert cfg.interval_seconds == 60.0
    assert cfg.lane_concurrency == 1


def test_sweeper_singleton_returns_same_instance() -> None:
    from universal_agent.services.mission_control_intelligence_sweeper import (
        get_sweeper,
        reset_sweeper_for_tests,
    )

    reset_sweeper_for_tests()
    a = get_sweeper()
    b = get_sweeper()
    assert a is b
    reset_sweeper_for_tests()
    c = get_sweeper()
    assert c is not a


def test_sweeper_tick_never_raises_even_on_handler_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sweeper must contain handler exceptions and surface them in
    `errors` rather than crashing the loop — otherwise one bad tile or
    one flaky LLM call would take out the whole intelligence pipeline.
    """
    from universal_agent.services.mission_control_intelligence_sweeper import (
        MissionControlSweeper,
        SweeperConfig,
    )

    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")

    class _BoomSweeper(MissionControlSweeper):
        def _run_tier0(self, result):  # type: ignore[override]
            raise RuntimeError("tier0 exploded")

    sweeper = _BoomSweeper(SweeperConfig())
    result = sweeper.tick()
    assert "tier0 exploded" in (result.errors[0] if result.errors else "")
    assert result.skipped_reason is None
