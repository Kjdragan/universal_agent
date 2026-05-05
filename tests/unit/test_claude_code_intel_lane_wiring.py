"""Tests for PR 17: ClaudeCodeIntelConfig reads handles from intel_lanes.yaml.

Resolution order (preserves v1 behavior):
  1. Explicit env override
  2. intel_lanes.yaml lane config
  3. DEFAULT_HANDLE / DEFAULT_HANDLES constants

Tests confirm each tier and that the env path is unchanged.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from universal_agent.services import claude_code_intel
from universal_agent.services.claude_code_intel import (
    DEFAULT_HANDLE,
    DEFAULT_HANDLES,
    ClaudeCodeIntelConfig,
    _all_handles_from_lane,
    _first_handle_from_lane,
)


# ── _first_handle_from_lane / _all_handles_from_lane ────────────────────────


def test_first_handle_from_lane_returns_first_configured(monkeypatch):
    # Force fresh lane cache to pick up any monkeypatched lane configs.
    from universal_agent.services import intel_lanes

    intel_lanes.reset_cache()
    handle = _first_handle_from_lane("claude-code-intelligence")
    # Default lane has handles = [ClaudeDevs, bcherny] — first is ClaudeDevs.
    assert handle == "ClaudeDevs"


def test_first_handle_from_lane_returns_empty_for_unknown_lane():
    assert _first_handle_from_lane("does-not-exist") == ""


def test_all_handles_from_lane_returns_all():
    handles = _all_handles_from_lane("claude-code-intelligence")
    assert "ClaudeDevs" in handles
    assert "bcherny" in handles


def test_all_handles_from_lane_returns_empty_for_unknown_lane():
    assert _all_handles_from_lane("does-not-exist") == []


# ── from_env: env override wins ─────────────────────────────────────────────


def test_from_env_uses_explicit_env_handle(monkeypatch):
    monkeypatch.setenv("UA_CLAUDE_CODE_INTEL_X_HANDLE", "OverrideHandle")
    monkeypatch.delenv("UA_CLAUDE_CODE_INTEL_LANE_SLUG", raising=False)
    config = ClaudeCodeIntelConfig.from_env()
    assert config.handle == "OverrideHandle"


def test_from_env_strips_at_sign_from_env_handle(monkeypatch):
    monkeypatch.setenv("UA_CLAUDE_CODE_INTEL_X_HANDLE", "@SomeHandle")
    config = ClaudeCodeIntelConfig.from_env()
    assert config.handle == "SomeHandle"


# ── from_env: lane fallback when env unset ──────────────────────────────────


def test_from_env_falls_back_to_lane_handle(monkeypatch):
    monkeypatch.delenv("UA_CLAUDE_CODE_INTEL_X_HANDLE", raising=False)
    monkeypatch.delenv("UA_CLAUDE_CODE_INTEL_LANE_SLUG", raising=False)
    config = ClaudeCodeIntelConfig.from_env()
    # Default lane's first handle is ClaudeDevs.
    assert config.handle == "ClaudeDevs"


def test_from_env_respects_lane_slug_override(monkeypatch):
    monkeypatch.delenv("UA_CLAUDE_CODE_INTEL_X_HANDLE", raising=False)
    # Future Codex lane is disabled but it still has handles defined.
    # Even disabled lanes are addressable by slug.
    monkeypatch.setenv("UA_CLAUDE_CODE_INTEL_LANE_SLUG", "openai-codex-intelligence")
    config = ClaudeCodeIntelConfig.from_env()
    assert config.lane_slug == "openai-codex-intelligence"
    # Codex lane's handles start with OpenAIDevs.
    assert config.handle == "OpenAIDevs"


def test_from_env_falls_back_to_default_when_lane_unknown(monkeypatch):
    monkeypatch.delenv("UA_CLAUDE_CODE_INTEL_X_HANDLE", raising=False)
    monkeypatch.setenv("UA_CLAUDE_CODE_INTEL_LANE_SLUG", "definitely-not-a-real-lane")
    config = ClaudeCodeIntelConfig.from_env()
    assert config.handle == DEFAULT_HANDLE


def test_from_env_records_lane_slug_in_config(monkeypatch):
    monkeypatch.delenv("UA_CLAUDE_CODE_INTEL_X_HANDLE", raising=False)
    monkeypatch.delenv("UA_CLAUDE_CODE_INTEL_LANE_SLUG", raising=False)
    config = ClaudeCodeIntelConfig.from_env()
    assert config.lane_slug == "claude-code-intelligence"


# ── from_lane: explicit slug ────────────────────────────────────────────────


def test_from_lane_uses_first_handle_from_lane():
    config = ClaudeCodeIntelConfig.from_lane("claude-code-intelligence")
    assert config.handle == "ClaudeDevs"
    assert config.lane_slug == "claude-code-intelligence"


def test_from_lane_falls_back_to_default_for_unknown():
    config = ClaudeCodeIntelConfig.from_lane("not-a-real-lane")
    assert config.handle == DEFAULT_HANDLE


# ── all_handles_from_env: env override wins ─────────────────────────────────


def test_all_handles_env_override_wins(monkeypatch):
    monkeypatch.setenv("UA_CLAUDE_CODE_INTEL_X_HANDLES", "Foo, @Bar, Baz")
    monkeypatch.delenv("UA_CLAUDE_CODE_INTEL_LANE_SLUG", raising=False)
    handles = ClaudeCodeIntelConfig.all_handles_from_env()
    assert handles == ["Foo", "Bar", "Baz"]


# ── all_handles_from_env: lane fallback when env unset ──────────────────────


def test_all_handles_falls_back_to_lane(monkeypatch):
    monkeypatch.delenv("UA_CLAUDE_CODE_INTEL_X_HANDLES", raising=False)
    monkeypatch.delenv("UA_CLAUDE_CODE_INTEL_LANE_SLUG", raising=False)
    handles = ClaudeCodeIntelConfig.all_handles_from_env()
    # Default lane: [ClaudeDevs, bcherny].
    assert handles == ["ClaudeDevs", "bcherny"]


def test_all_handles_lane_slug_override(monkeypatch):
    monkeypatch.delenv("UA_CLAUDE_CODE_INTEL_X_HANDLES", raising=False)
    monkeypatch.setenv("UA_CLAUDE_CODE_INTEL_LANE_SLUG", "openai-codex-intelligence")
    handles = ClaudeCodeIntelConfig.all_handles_from_env()
    # Codex lane: [OpenAIDevs, OpenAI, sama] from intel_lanes.yaml.
    assert "OpenAIDevs" in handles
    assert "ClaudeDevs" not in handles


def test_all_handles_falls_back_to_constants_when_lane_unknown(monkeypatch):
    monkeypatch.delenv("UA_CLAUDE_CODE_INTEL_X_HANDLES", raising=False)
    monkeypatch.setenv("UA_CLAUDE_CODE_INTEL_LANE_SLUG", "definitely-not-a-real-lane")
    handles = ClaudeCodeIntelConfig.all_handles_from_env()
    assert handles == list(DEFAULT_HANDLES)


def test_all_handles_empty_env_is_treated_as_unset(monkeypatch):
    """UA_CLAUDE_CODE_INTEL_X_HANDLES='' should NOT short-circuit to empty."""
    monkeypatch.setenv("UA_CLAUDE_CODE_INTEL_X_HANDLES", "")
    monkeypatch.delenv("UA_CLAUDE_CODE_INTEL_LANE_SLUG", raising=False)
    handles = ClaudeCodeIntelConfig.all_handles_from_env()
    assert handles == ["ClaudeDevs", "bcherny"]


def test_default_lane_matches_default_constants():
    """Sanity: lane config and DEFAULT_HANDLES must agree for the canonical lane.

    Drift here would mean swapping env-driven for lane-driven changes
    behavior, which is a regression.
    """
    lane_handles = _all_handles_from_lane("claude-code-intelligence")
    assert lane_handles == list(DEFAULT_HANDLES)
