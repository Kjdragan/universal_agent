"""Tests for the deterministic wiki-rescue policy (pure decision core)."""

from __future__ import annotations

import pytest

from universal_agent.services.wiki_rescue_policy import (
    ACTION_ESCALATE,
    ACTION_HANDOFF_CODY,
    ACTION_RETRY_ATLAS,
    ACTION_SKIP,
    ATLAS_VP,
    CODY_VP,
    MAX_ATLAS_RETRIES,
    MAX_TOTAL_RESCUES,
    decide_wiki_rescue,
)


def _decide(mode="timeout", count=1, mission_type="proactive_wiki", cody=True):
    return decide_wiki_rescue(
        mission_type=mission_type,
        failure_mode=mode,
        failure_count=count,
        cody_available=cody,
    )


# --- scope / skip ----------------------------------------------------------

def test_non_wiki_mission_is_skipped():
    d = _decide(mission_type="tutorial_build")
    assert d.action == ACTION_SKIP


def test_operator_cancel_is_skipped():
    d = _decide(mode="operator_cancel")
    assert d.action == ACTION_SKIP
    assert "deliberate" in d.reason


# --- step 1: transient -> ATLAS retry, bounded -----------------------------

@pytest.mark.parametrize("count", [1, 2])
def test_transient_retries_on_atlas_within_budget(count):
    d = _decide(mode="timeout", count=count)
    assert d.action == ACTION_RETRY_ATLAS
    assert d.target_vp == ATLAS_VP


def test_transient_subprocess_crash_is_transient():
    assert _decide(mode="subprocess_crash", count=1).action == ACTION_RETRY_ATLAS


def test_stale_claim_expired_is_transient():
    assert _decide(mode="stale_claim_expired", count=1).action == ACTION_RETRY_ATLAS


def test_stale_prefix_variants_are_transient():
    assert _decide(mode="stale_lease_lost", count=1).action == ACTION_RETRY_ATLAS


# --- step 2: exhausted/structural -> Cody (or ATLAS fallback) ---------------

def test_atlas_retries_exhausted_hands_to_cody_when_free():
    d = _decide(mode="timeout", count=MAX_ATLAS_RETRIES + 1, cody=True)
    assert d.action == ACTION_HANDOFF_CODY
    assert d.target_vp == CODY_VP


def test_atlas_retries_exhausted_falls_back_to_atlas_when_cody_busy():
    d = _decide(mode="timeout", count=MAX_ATLAS_RETRIES + 1, cody=False)
    assert d.action == ACTION_RETRY_ATLAS
    assert d.target_vp == ATLAS_VP
    assert "Cody busy" in d.reason


def test_structural_failure_skips_atlas_and_goes_to_cody():
    # workspace_guard is structural — no blind ATLAS retry even at count 1.
    d = _decide(mode="workspace_guard", count=1, cody=True)
    assert d.action == ACTION_HANDOFF_CODY
    assert d.target_vp == CODY_VP


def test_structural_failure_cody_busy_falls_back_to_atlas():
    d = _decide(mode="goal_cap_hit", count=1, cody=False)
    assert d.action == ACTION_RETRY_ATLAS


def test_unknown_mode_treated_structural():
    # An unrecognized / ambiguous mode is NOT blind-retried — Cody diagnoses.
    d = _decide(mode="vp_self_reported", count=1, cody=True)
    assert d.action == ACTION_HANDOFF_CODY


# --- step 3: budget exhausted -> escalate ----------------------------------

def test_budget_exhausted_escalates():
    d = _decide(mode="timeout", count=MAX_TOTAL_RESCUES + 1)
    assert d.action == ACTION_ESCALATE
    assert d.target_vp is None


def test_escalation_does_not_depend_on_cody_availability():
    assert _decide(count=MAX_TOTAL_RESCUES + 1, cody=False).action == ACTION_ESCALATE
    assert _decide(count=MAX_TOTAL_RESCUES + 1, cody=True).action == ACTION_ESCALATE


# --- full chain walk (the bounded ladder) ----------------------------------

def test_full_transient_chain_ladder_with_cody_free():
    # count 1,2 -> ATLAS; 3 -> Cody; 4 -> escalate. Never unbounded.
    assert _decide(count=1).action == ACTION_RETRY_ATLAS
    assert _decide(count=2).action == ACTION_RETRY_ATLAS
    assert _decide(count=3).action == ACTION_HANDOFF_CODY
    assert _decide(count=4).action == ACTION_ESCALATE
