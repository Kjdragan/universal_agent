"""Tests for VP detection logic used in prompt routing.

Covers: agent_setup.py VP detection markers
"""
from __future__ import annotations


def _is_vp_worker(soul_context: str | None) -> bool:
    """Replicates the detection logic from agent_setup.py."""
    return any(
        marker in (soul_context or "")
        for marker in ("CODIE", "ATLAS", "VP Coder Agent", "VP General Agent")
    )


def test_codie_marker_detected():
    assert _is_vp_worker("You are CODIE, the autonomous VP Coder Agent.") is True


def test_atlas_marker_detected():
    assert _is_vp_worker("You are ATLAS, the VP General Agent for broad missions.") is True


def test_vp_coder_agent_marker_detected():
    assert _is_vp_worker("I am a VP Coder Agent focused on implementation.") is True


def test_vp_general_agent_marker_detected():
    assert _is_vp_worker("I am a VP General Agent that handles research.") is True


def test_simone_not_detected_as_vp():
    """Simone's soul should not trigger VP detection."""
    simone_soul = (
        "You are Simone, the primary orchestrator and coordinator "
        "of the Universal Agent project. You manage all sessions."
    )
    assert _is_vp_worker(simone_soul) is False


def test_empty_soul_not_detected():
    assert _is_vp_worker("") is False
    assert _is_vp_worker(None) is False


def test_random_text_not_detected():
    assert _is_vp_worker("Just a regular agent with no VP markers.") is False
