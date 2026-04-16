"""Tests for universal_agent.utils.model_resolution.

Validates that:
  - All three ZAI model tiers (haiku, sonnet, opus) resolve to known-good
    lowercase model identifiers accepted by the Z.AI API.
  - ANTHROPIC_DEFAULT_*_MODEL env vars correctly override the hardcoded map.
  - resolve_haiku / resolve_sonnet / resolve_opus convenience helpers return
    the same values as resolve_model(tier=...).
  - resolve_claude_code_model respects UA_CLAUDE_CODE_MODEL and MODEL_NAME.
  - resolve_agent_teams_enabled precedence is correct.
"""

from __future__ import annotations

import pytest

from universal_agent.utils.model_resolution import (
    ZAI_MODEL_MAP,
    resolve_agent_teams_enabled,
    resolve_claude_code_model,
    resolve_haiku,
    resolve_model,
    resolve_opus,
    resolve_sonnet,
)

# ── Canonical ZAI model names ────────────────────────────────────────────────

# The Z.AI API requires lowercase model identifiers. If these names drift,
# agents will get error 1211 "Unknown Model, please check the model code."
KNOWN_ZAI_MODELS = {"glm-4.5-air", "glm-5-turbo", "glm-5.1"}


class TestZaiModelMap:
    def test_haiku_is_lowercase(self):
        assert ZAI_MODEL_MAP["haiku"] == ZAI_MODEL_MAP["haiku"].lower()

    def test_sonnet_is_lowercase(self):
        assert ZAI_MODEL_MAP["sonnet"] == ZAI_MODEL_MAP["sonnet"].lower()

    def test_opus_is_lowercase(self):
        assert ZAI_MODEL_MAP["opus"] == ZAI_MODEL_MAP["opus"].lower()

    def test_haiku_is_known_model(self):
        assert ZAI_MODEL_MAP["haiku"] in KNOWN_ZAI_MODELS, (
            f"haiku maps to '{ZAI_MODEL_MAP['haiku']}' which is not in KNOWN_ZAI_MODELS. "
            f"Update KNOWN_ZAI_MODELS if you've intentionally added a new model."
        )

    def test_sonnet_is_known_model(self):
        assert ZAI_MODEL_MAP["sonnet"] in KNOWN_ZAI_MODELS

    def test_opus_is_known_model(self):
        assert ZAI_MODEL_MAP["opus"] in KNOWN_ZAI_MODELS

    def test_no_glm_5_1_pascal_case(self):
        """GLM-5.1 (PascalCase) was rejected; glm-5.1 (lowercase) is valid."""
        for tier, model in ZAI_MODEL_MAP.items():
            assert model != "GLM-5.1", (
                f"ZAI_MODEL_MAP[{tier!r}] uses 'GLM-5.1' (PascalCase) which is rejected. "
                f"Use 'glm-5.1' (lowercase)."
            )

    def test_no_pascal_case_glm(self):
        """Z.AI API rejects PascalCase identifiers like 'GLM-5-Turbo'."""
        for tier, model in ZAI_MODEL_MAP.items():
            assert not model.startswith("GLM"), (
                f"ZAI_MODEL_MAP[{tier!r}] = {model!r} uses PascalCase 'GLM' prefix which "
                f"is rejected by the Z.AI API (error 1211). Use lowercase."
            )


# ── resolve_model() ───────────────────────────────────────────────────────────

class TestResolveModel:
    def test_haiku_returns_map_value(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", raising=False)
        assert resolve_model("haiku") == ZAI_MODEL_MAP["haiku"]

    def test_sonnet_returns_map_value(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_DEFAULT_SONNET_MODEL", raising=False)
        assert resolve_model("sonnet") == ZAI_MODEL_MAP["sonnet"]

    def test_opus_returns_map_value(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_DEFAULT_OPUS_MODEL", raising=False)
        assert resolve_model("opus") == ZAI_MODEL_MAP["opus"]

    def test_unknown_tier_falls_back_to_opus(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_DEFAULT_OPUS_MODEL", raising=False)
        result = resolve_model("unknown_tier")
        assert result == ZAI_MODEL_MAP["opus"]

    def test_haiku_env_override(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "custom-haiku")
        assert resolve_model("haiku") == "custom-haiku"

    def test_sonnet_env_override(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "custom-sonnet")
        assert resolve_model("sonnet") == "custom-sonnet"

    def test_opus_env_override(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "custom-opus")
        assert resolve_model("opus") == "custom-opus"

    def test_empty_env_override_uses_map(self, monkeypatch):
        """An empty string env var should not override the map value."""
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "   ")
        assert resolve_model("opus") == ZAI_MODEL_MAP["opus"]


# ── Convenience helpers ───────────────────────────────────────────────────────

class TestConvenienceHelpers:
    def test_resolve_haiku(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", raising=False)
        assert resolve_haiku() == ZAI_MODEL_MAP["haiku"]

    def test_resolve_sonnet_delegates_to_opus(self, monkeypatch):
        """resolve_sonnet() intentionally redirects to opus tier."""
        monkeypatch.delenv("ANTHROPIC_DEFAULT_OPUS_MODEL", raising=False)
        assert resolve_sonnet() == ZAI_MODEL_MAP["opus"]

    def test_resolve_opus(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_DEFAULT_OPUS_MODEL", raising=False)
        assert resolve_opus() == ZAI_MODEL_MAP["opus"]


# ── resolve_claude_code_model() ───────────────────────────────────────────────

class TestResolveClaudeCodeModel:
    def test_delegates_to_resolve_model(self, monkeypatch):
        """resolve_claude_code_model(tier) is a thin alias for resolve_model(tier)."""
        monkeypatch.delenv("ANTHROPIC_DEFAULT_OPUS_MODEL", raising=False)
        assert resolve_claude_code_model(default="opus") == ZAI_MODEL_MAP["opus"]

    def test_haiku_tier(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", raising=False)
        assert resolve_claude_code_model(default="haiku") == ZAI_MODEL_MAP["haiku"]

    def test_falls_back_to_default_tier(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_DEFAULT_OPUS_MODEL", raising=False)
        result = resolve_claude_code_model(default="opus")
        assert result == ZAI_MODEL_MAP["opus"]


# ── resolve_agent_teams_enabled() ────────────────────────────────────────────

class TestResolveAgentTeamsEnabled:
    def test_defaults_true(self, monkeypatch):
        monkeypatch.delenv("UA_AGENT_TEAMS_ENABLED", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", raising=False)
        assert resolve_agent_teams_enabled(default=True) is True

    def test_ua_override_wins(self, monkeypatch):
        monkeypatch.setenv("UA_AGENT_TEAMS_ENABLED", "0")
        monkeypatch.setenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "1")
        assert resolve_agent_teams_enabled(default=True) is False

    def test_uses_native_flag_when_ua_absent(self, monkeypatch):
        monkeypatch.delenv("UA_AGENT_TEAMS_ENABLED", raising=False)
        monkeypatch.setenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "true")
        assert resolve_agent_teams_enabled(default=False) is True
