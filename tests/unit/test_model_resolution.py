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
    model_call_timeout_seconds,
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

    def test_unknown_tier_falls_back_to_sonnet(self, monkeypatch):
        """Default tier is sonnet per operator decision: the global default
        should be the balanced mid tier, with heavy work opting into opus
        explicitly."""
        monkeypatch.delenv("ANTHROPIC_DEFAULT_SONNET_MODEL", raising=False)
        result = resolve_model("unknown_tier")
        assert result == ZAI_MODEL_MAP["sonnet"]

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

    def test_resolve_haiku_is_glm_4_5_air(self, monkeypatch):
        """OPERATOR LOCK (Kevin, 2026-06-05): the haiku tier MUST map to
        glm-4.5-air. glm-4.5-air has been tested repeatedly and works;
        do not remap it. This guard fails loudly if someone reverts the
        mapping to glm-5-turbo (or anything else)."""
        monkeypatch.delenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", raising=False)
        assert resolve_haiku() == "glm-4.5-air"

    def test_resolve_sonnet_returns_real_sonnet(self, monkeypatch):
        """Previously this had a forced override returning opus —
        silently promoting every direct caller. Restored to honest
        behavior: sonnet means sonnet (glm-5-turbo)."""
        monkeypatch.delenv("ANTHROPIC_DEFAULT_SONNET_MODEL", raising=False)
        assert resolve_sonnet() == ZAI_MODEL_MAP["sonnet"]
        # Belt-and-suspenders: must not return the opus mapping.
        assert resolve_sonnet() != ZAI_MODEL_MAP["opus"]

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

class TestResolveClaudeCodeModelDefault:
    def test_default_tier_is_sonnet(self, monkeypatch):
        """Per operator decision: global daemon default is sonnet, not opus.
        Heavy-tier subagents request opus explicitly via their YAML."""
        monkeypatch.delenv("ANTHROPIC_DEFAULT_SONNET_MODEL", raising=False)
        # Calling with no argument should land on sonnet.
        assert resolve_claude_code_model() == ZAI_MODEL_MAP["sonnet"]


class TestModelCallTimeoutSeconds:
    def test_haiku_default_is_120s(self, monkeypatch):
        monkeypatch.delenv("UA_MODEL_TIMEOUT_HAIKU_SECONDS", raising=False)
        assert model_call_timeout_seconds("haiku") == 120.0

    def test_sonnet_default_is_180s(self, monkeypatch):
        monkeypatch.delenv("UA_MODEL_TIMEOUT_SONNET_SECONDS", raising=False)
        assert model_call_timeout_seconds("sonnet") == 180.0

    def test_opus_default_is_generous(self, monkeypatch):
        # 2026-05-24: bumped from 300s to 1800s after paper_to_podcast_daily
        # was killed 6 nights running at the old knife-edge cap. Operating
        # principle: opus default should be generous because real opus work
        # (research, multi-doc synthesis, long crons) legitimately takes
        # 10+ minutes. Haiku/sonnet stay tight on purpose.
        monkeypatch.delenv("UA_MODEL_TIMEOUT_OPUS_SECONDS", raising=False)
        assert model_call_timeout_seconds("opus") == 1800.0

    def test_env_override_haiku(self, monkeypatch):
        monkeypatch.setenv("UA_MODEL_TIMEOUT_HAIKU_SECONDS", "45")
        assert model_call_timeout_seconds("haiku") == 45.0

    def test_unknown_tier_falls_back_to_sonnet(self, monkeypatch):
        monkeypatch.delenv("UA_MODEL_TIMEOUT_SONNET_SECONDS", raising=False)
        assert model_call_timeout_seconds("frobnitz") == 180.0

    def test_zero_disables_cap(self, monkeypatch):
        monkeypatch.setenv("UA_MODEL_TIMEOUT_OPUS_SECONDS", "0")
        assert model_call_timeout_seconds("opus") == 0.0


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


# ── model_id_to_tier() — wire-id → limiter tier reverse map ──────────────────

from universal_agent.utils.model_resolution import model_id_to_tier


@pytest.fixture
def clean_tier_env(monkeypatch):
    """Clear every env var model_id_to_tier consults and reset the once-per-
    process conflict-warned set so tests don't leak warnings into each other."""
    import universal_agent.utils.model_resolution as mr

    for var in (
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "UA_MISSION_CONTROL_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    mr._TIER_CONFLICT_WARNED.clear()
    yield
    mr._TIER_CONFLICT_WARNED.clear()


class TestModelIdToTier:
    # Rule 1 — exact reverse ZAI_MODEL_MAP match (wire identity).
    def test_air_is_haiku(self, clean_tier_env):
        assert model_id_to_tier("glm-4.5-air") == "haiku"

    def test_turbo_is_sonnet(self, clean_tier_env):
        assert model_id_to_tier("glm-5-turbo") == "sonnet"

    def test_glm51_is_opus(self, clean_tier_env):
        assert model_id_to_tier("glm-5.1") == "opus"

    def test_case_insensitive_reverse_map(self, clean_tier_env):
        assert model_id_to_tier("GLM-5.1") == "opus"
        assert model_id_to_tier("Glm-5-Turbo") == "sonnet"

    # Rule 2 — literal mid-tier ids that bypass ZAI_MODEL_MAP.
    def test_glm47_is_mid(self, clean_tier_env):
        assert model_id_to_tier("glm-4.7") == "mid"

    def test_glm46_is_mid(self, clean_tier_env):
        assert model_id_to_tier("glm-4.6") == "mid"

    # Rule 1 OUTRANKS env intent — the inversion-protection cases.
    def test_wire_identity_wins_over_opus_env(self, clean_tier_env, monkeypatch):
        """ANTHROPIC_DEFAULT_OPUS_MODEL=glm-5-turbo must NOT promote glm-5-turbo
        to opus — wire identity wins, so it stays sonnet. Env-first would let
        one caller's intent capture every caller of that wire id."""
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "glm-5-turbo")
        assert model_id_to_tier("glm-5-turbo") == "sonnet"

    def test_wire_identity_wins_over_haiku_env(self, clean_tier_env, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "glm-5.1")
        assert model_id_to_tier("glm-5.1") == "opus"

    # Rule 3 — env vars classify UNKNOWN ids (no wire match).
    def test_env_claims_unknown_id_opus(self, clean_tier_env, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "some-private-opus")
        assert model_id_to_tier("some-private-opus") == "opus"

    def test_env_claims_unknown_id_haiku(self, clean_tier_env, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "private-haiku")
        assert model_id_to_tier("private-haiku") == "haiku"

    def test_env_claims_unknown_id_mid(self, clean_tier_env, monkeypatch):
        monkeypatch.setenv("UA_MISSION_CONTROL_MODEL", "private-mc")
        assert model_id_to_tier("private-mc") == "mid"

    # Rule 3 conflict — multiple env vars claim the same unknown id → most
    # conservative (opus < sonnet < mid < haiku).
    def test_conflict_resolves_to_most_conservative(self, clean_tier_env, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "shared-mystery")
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "shared-mystery")
        assert model_id_to_tier("shared-mystery") == "opus"

    def test_conflict_sonnet_vs_haiku_picks_sonnet(self, clean_tier_env, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "ambig")
        monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "ambig")
        assert model_id_to_tier("ambig") == "sonnet"

    def test_conflict_warns_once_per_process(self, clean_tier_env, monkeypatch, caplog):
        import logging

        monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "warned-id")
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "warned-id")
        with caplog.at_level(logging.WARNING, logger="universal_agent.utils.model_resolution"):
            model_id_to_tier("warned-id")
            model_id_to_tier("warned-id")
        conflict_warnings = [r for r in caplog.records if "claimed by multiple" in r.message]
        assert len(conflict_warnings) == 1

    # Rule 4 — safe defaults.
    def test_unknown_id_defaults_to_sonnet(self, clean_tier_env):
        assert model_id_to_tier("totally-unknown-model") == "sonnet"

    def test_none_defaults_to_sonnet(self, clean_tier_env):
        assert model_id_to_tier(None) == "sonnet"

    def test_empty_defaults_to_sonnet(self, clean_tier_env):
        assert model_id_to_tier("") == "sonnet"
        assert model_id_to_tier("   ") == "sonnet"

    def test_tier_set_matches_timeout_tiers_plus_mid(self):
        """The tier set is {opus, sonnet, mid, haiku} — model_call_timeout_seconds
        tiers (opus/sonnet/haiku) plus the new mid."""
        produced = {
            model_id_to_tier("glm-5.1"),
            model_id_to_tier("glm-5-turbo"),
            model_id_to_tier("glm-4.5-air"),
            model_id_to_tier("glm-4.7"),
        }
        assert produced == {"opus", "sonnet", "haiku", "mid"}
