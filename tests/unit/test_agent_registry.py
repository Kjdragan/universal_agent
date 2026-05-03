"""Tests for universal_agent.services.agent_registry."""

from __future__ import annotations

import pytest

from universal_agent.services.agent_registry import (
    AGENT_REGISTRY,
    AgentMeta,
    get_agent_meta,
    get_display_name,
    get_expected_skills,
    get_next_step_hint,
    is_bowser_agent,
    is_foreground_only,
)


# ---------------------------------------------------------------------------
# AgentMeta dataclass
# ---------------------------------------------------------------------------


class TestAgentMeta:
    def test_frozen_default_fields(self):
        meta = AgentMeta(name="test-agent")
        assert meta.display_name == ""
        assert meta.foreground_only is False
        assert meta.is_bowser is False
        assert meta.expected_skills == []
        assert meta.next_step_hint == ""
        assert meta.display_name_prefixes == []

    def test_frozen_immutable(self):
        meta = AgentMeta(name="test-agent")
        with pytest.raises(AttributeError):
            meta.name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# get_agent_meta
# ---------------------------------------------------------------------------


class TestGetAgentMeta:
    def test_known_type(self):
        meta = get_agent_meta("research-specialist")
        assert meta.name == "research-specialist"
        assert meta.foreground_only is True

    def test_unknown_type_returns_default(self):
        meta = get_agent_meta("nonexistent-agent")
        assert meta.name == "nonexistent-agent"
        assert meta.display_name == ""
        assert meta.foreground_only is False

    def test_known_type_preserves_all_fields(self):
        meta = get_agent_meta("image-expert")
        assert "image-generation" in meta.expected_skills
        assert meta.next_step_hint  # non-empty hint


# ---------------------------------------------------------------------------
# is_foreground_only
# ---------------------------------------------------------------------------


class TestIsForegroundOnly:
    @pytest.mark.parametrize(
        "agent_type, expected",
        [
            ("research-specialist", True),
            ("report-writer", True),
            ("arxiv-specialist", True),
            ("data-analyst", False),
            ("image-expert", False),
            ("nonexistent-agent", False),
        ],
    )
    def test_parametrized(self, agent_type: str, expected: bool):
        assert is_foreground_only(agent_type) is expected


# ---------------------------------------------------------------------------
# is_bowser_agent
# ---------------------------------------------------------------------------


class TestIsBowserAgent:
    @pytest.mark.parametrize(
        "agent_type, expected",
        [
            ("claude-bowser-agent", True),
            ("playwright-bowser-agent", True),
            ("bowser-qa-agent", True),
            ("research-specialist", False),
            ("nonexistent-agent", False),
        ],
    )
    def test_parametrized(self, agent_type: str, expected: bool):
        assert is_bowser_agent(agent_type) is expected


# ---------------------------------------------------------------------------
# get_expected_skills
# ---------------------------------------------------------------------------


class TestGetExpectedSkills:
    def test_returns_copy(self):
        skills = get_expected_skills("image-expert")
        assert skills == ["image-generation"]
        # Mutating the returned list should not affect the registry.
        skills.append("forged")
        assert get_expected_skills("image-expert") == ["image-generation"]

    def test_unknown_returns_empty(self):
        assert get_expected_skills("nonexistent-agent") == []

    def test_agent_with_multiple_skills(self):
        skills = get_expected_skills("youtube-expert")
        assert "youtube-transcript-metadata" in skills
        assert "youtube-tutorial-creation" in skills


# ---------------------------------------------------------------------------
# get_next_step_hint
# ---------------------------------------------------------------------------


class TestGetNextStepHint:
    def test_known_has_hint(self):
        hint = get_next_step_hint("research-specialist")
        assert "research" in hint.lower() or "delegate" in hint.lower()

    def test_unknown_returns_empty(self):
        assert get_next_step_hint("nonexistent-agent") == ""


# ---------------------------------------------------------------------------
# get_display_name
# ---------------------------------------------------------------------------


class TestGetDisplayName:
    def test_empty_string(self):
        assert get_display_name("") == "Subagent"

    def test_known_with_display_name(self):
        assert get_display_name("research-specialist") == "Research Specialist"
        assert get_display_name("report-writer") == "Report Writer"

    def test_known_without_display_name(self):
        name = get_display_name("video-creation-expert")
        assert isinstance(name, str)
        assert len(name) > 0

    def test_prefix_fallback(self):
        assert get_display_name("research-foo") == "Research Specialist"

    def test_no_match_falls_back(self):
        assert get_display_name("totally-unknown-agent") == "Subagent: totally-unknown-agent"


# ---------------------------------------------------------------------------
# Registry integrity
# ---------------------------------------------------------------------------


class TestRegistryIntegrity:
    def test_all_entries_have_valid_name(self):
        for key, meta in AGENT_REGISTRY.items():
            assert meta.name == key, f"Registry key {key!r} has mismatched name {meta.name!r}"

    def test_no_duplicate_display_name_prefixes(self):
        seen: dict[str, str] = {}
        for key, meta in AGENT_REGISTRY.items():
            for prefix in meta.display_name_prefixes:
                if prefix in seen:
                    pytest.fail(
                        f"Duplicate prefix {prefix!r} in both {seen[prefix]!r} and {key!r}"
                    )
                seen[prefix] = key
