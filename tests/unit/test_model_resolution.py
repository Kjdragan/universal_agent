from __future__ import annotations

from universal_agent.utils.model_resolution import (
    resolve_agent_teams_enabled,
    resolve_claude_code_model,
)


def test_resolve_claude_code_model_prefers_ua_override(monkeypatch):
    monkeypatch.setenv("UA_CLAUDE_CODE_MODEL", "opus")
    monkeypatch.setenv("MODEL_NAME", "claude-sonnet-legacy")
    assert resolve_claude_code_model(default="sonnet") == "opus"


def test_resolve_claude_code_model_falls_back_to_model_name(monkeypatch):
    monkeypatch.delenv("UA_CLAUDE_CODE_MODEL", raising=False)
    monkeypatch.setenv("MODEL_NAME", "custom-model")
    assert resolve_claude_code_model(default="sonnet") == "custom-model"


def test_resolve_agent_teams_enabled_defaults_true(monkeypatch):
    monkeypatch.delenv("UA_AGENT_TEAMS_ENABLED", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", raising=False)
    assert resolve_agent_teams_enabled(default=True) is True


def test_resolve_agent_teams_enabled_ua_override_wins(monkeypatch):
    monkeypatch.setenv("UA_AGENT_TEAMS_ENABLED", "0")
    monkeypatch.setenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "1")
    assert resolve_agent_teams_enabled(default=True) is False


def test_resolve_agent_teams_enabled_uses_native_flag(monkeypatch):
    monkeypatch.delenv("UA_AGENT_TEAMS_ENABLED", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "true")
    assert resolve_agent_teams_enabled(default=False) is True
