"""Tests for brittle routing heuristic cleanup.

Covers three changes:
  1. Removed redundant regex patterns from _EXPLICIT_*_VP_PATTERNS
  2. Removed redundant heartbeat* entries from _PROMPT_INFERRED_VP_BLOCKED_RUN_KINDS
  3. Replaced inline hardcoded source set with canonical constant
"""

from __future__ import annotations

import pytest

from universal_agent.gateway import (
    _EXPLICIT_CODER_VP_PATTERNS,
    _EXPLICIT_GENERAL_VP_PATTERNS,
    _PROMPT_INFERRED_VP_BLOCKED_RUN_KINDS,
    _PROMPT_INFERRED_VP_BLOCKED_SOURCES,
    _allow_prompt_inferred_vp_routing,
    _infer_explicit_vp_target,
)

# ---------------------------------------------------------------------------
# Change 1: Simplified regex patterns
# ---------------------------------------------------------------------------


class TestGeneralVPPatterns:
    """Verify the simplified general VP patterns match all required inputs."""

    @pytest.mark.parametrize(
        "text",
        [
            "general vp",
            "generalist VP",
            "General VP",
            "GENERALIST VP",
            "vp general",
            "VP Generalist",
            "vp general agent",
            "VP Generalist Agent",
            "vp.general.primary",
            "use the generalist VP",
            "use VP general",
            "Use the general VP for this task",
            "I want to use vp general",
            "delegate to vp.general.primary",
        ],
    )
    def test_matches_general_vp_phrases(self, text):
        assert any(p.search(text) for p in _EXPLICIT_GENERAL_VP_PATTERNS), (
            f"Expected general VP match for: {text!r}"
        )

    @pytest.mark.parametrize(
        "text",
        [
            "general topics",
            "something about general knowledge",
            "vp of engineering",
            "no match here",
        ],
    )
    def test_does_not_match_non_vp_phrases(self, text):
        assert not any(p.search(text) for p in _EXPLICIT_GENERAL_VP_PATTERNS), (
            f"Unexpected general VP match for: {text!r}"
        )

    def test_pattern_count_is_minimal(self):
        """Only 3 patterns: base 'general VP', base 'VP general', and 'vp.general.primary'."""
        assert len(_EXPLICIT_GENERAL_VP_PATTERNS) == 3


class TestCoderVPPatterns:
    """Verify the simplified coder VP patterns match all required inputs."""

    @pytest.mark.parametrize(
        "text",
        [
            "coder vp",
            "VP Coder",
            "vp coder agent",
            "VP Coder Agent",
            "codie",
            "CODIE",
            "codie refactor this",
            "vp.coder.primary",
            "use the coder VP",
            "use VP coder",
            "Use the VP Coder for this",
        ],
    )
    def test_matches_coder_vp_phrases(self, text):
        assert any(p.search(text) for p in _EXPLICIT_CODER_VP_PATTERNS), (
            f"Expected coder VP match for: {text!r}"
        )

    @pytest.mark.parametrize(
        "text",
        [
            "coding topics",
            "something about coding",
            "no match here",
            "codie_dev",  # underscore breaks word boundary
        ],
    )
    def test_does_not_match_non_vp_phrases(self, text):
        assert not any(p.search(text) for p in _EXPLICIT_CODER_VP_PATTERNS), (
            f"Unexpected coder VP match for: {text!r}"
        )

    def test_pattern_count_is_minimal(self):
        """Only 4 patterns: base 'coder VP', base 'VP coder', 'codie', and 'vp.coder.primary'."""
        assert len(_EXPLICIT_CODER_VP_PATTERNS) == 4


class TestInferExplicitVPTarget:
    """End-to-end tests for _infer_explicit_vp_target with simplified patterns."""

    @pytest.mark.parametrize(
        "text",
        [
            "Use the general VP to write a story",
            "delegate to vp general",
            "vp.general.primary should handle this",
            "ask generalist VP",
        ],
    )
    def test_infers_general_vp(self, text):
        vp_id, mission_type = _infer_explicit_vp_target(text)
        assert vp_id == "vp.general.primary"
        assert mission_type == "general_task"

    @pytest.mark.parametrize(
        "text",
        [
            "Use the VP Coder agent to refactor this module",
            "ask codie to fix the bug",
            "vp.coder.primary should handle this",
            "coder VP refactor the auth module",
        ],
    )
    def test_infers_coder_vp(self, text):
        vp_id, mission_type = _infer_explicit_vp_target(text)
        assert vp_id is not None
        assert mission_type == "coding_task"

    @pytest.mark.parametrize(
        "text",
        [
            "Use the general DP to write a story",
            "general topics",
            "write some code",
            "",
            "   ",
        ],
    )
    def test_does_not_infer_for_non_matching(self, text):
        vp_id, mission_type = _infer_explicit_vp_target(text)
        assert vp_id is None
        assert mission_type is None


# ---------------------------------------------------------------------------
# Change 2: Removed redundant heartbeat* entries from blocked run kinds
# ---------------------------------------------------------------------------


class TestBlockedRunKinds:
    """Verify heartbeat* variants are still blocked via prefix check."""

    def test_heartbeat_variants_not_in_explicit_set(self):
        """The explicit set should not contain heartbeat* entries."""
        for entry in _PROMPT_INFERRED_VP_BLOCKED_RUN_KINDS:
            assert not entry.startswith("heartbeat"), (
                f"heartbeat* entry {entry!r} should not be in explicit set"
            )

    @pytest.mark.parametrize(
        "run_kind",
        [
            "heartbeat",
            "heartbeat_email_wake",
            "heartbeat_cron_wake",
            "heartbeat_custom_variant",
        ],
    )
    def test_heartbeat_run_kinds_still_blocked(self, run_kind):
        """All heartbeat* variants should be blocked by the prefix check."""
        assert _allow_prompt_inferred_vp_routing(
            request_source="user",
            request_run_kind=run_kind,
        ) is False

    @pytest.mark.parametrize(
        "run_kind",
        [
            "todo_execution",
            "email_triage",
            "hook",
            "task_run",
            "cron_job_dispatch",
        ],
    )
    def test_non_heartbeat_blocked_kinds(self, run_kind):
        """Non-heartbeat blocked kinds should still be blocked."""
        assert _allow_prompt_inferred_vp_routing(
            request_source="user",
            request_run_kind=run_kind,
        ) is False

    def test_regular_run_kind_allowed(self):
        assert _allow_prompt_inferred_vp_routing(
            request_source="user",
            request_run_kind="user",
        ) is True


# ---------------------------------------------------------------------------
# Change 3: Inline source set replaced with canonical constant
# ---------------------------------------------------------------------------


class TestBlockedSources:
    """Verify the canonical source set matches all expected entries."""

    EXPECTED_SOURCES = frozenset({
        "cron",
        "webhook",
        "heartbeat",
        "heartbeat_synthetic",
        "task_run",
        "email_hook",
        "todo_dispatcher",
    })

    def test_canonical_set_has_all_expected_sources(self):
        assert _PROMPT_INFERRED_VP_BLOCKED_SOURCES == self.EXPECTED_SOURCES

    @pytest.mark.parametrize("source", list(EXPECTED_SOURCES))
    def test_all_sources_are_blocked(self, source):
        assert _allow_prompt_inferred_vp_routing(
            request_source=source,
            request_run_kind="user",
        ) is False

    def test_user_source_is_allowed(self):
        assert _allow_prompt_inferred_vp_routing(
            request_source="user",
            request_run_kind="",
        ) is True
