"""Unit tests for the self-briefing helper service (PRD § 5.1, 5.2, 5.5).

Covers:
- ``vp_goal_enabled()`` reads the feature flag correctly.
- ``is_goal_eligible_mission()`` enforces flag + Cody-only + eligible source_kind.
- ``build_self_briefing_prompt()`` produces well-formed briefing-turn prompts.
- ``read_goal_condition()`` validates ≤4000 chars, non-empty.
- ``check_completion_attestation()`` is the safety net for COMPLETION.md.
- ``_build_cli_prompt()`` injects self-briefing + COMPLETION directive when flag is on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Explicit import so monkeypatch can resolve dotted paths on fresh interpreter.
from universal_agent.services import self_briefing  # noqa: F401
from universal_agent.services.self_briefing import (
    GOAL_CONDITION_MAX_CHARS,
    GOAL_ELIGIBLE_SOURCE_KINDS,
    build_self_briefing_prompt,
    check_completion_attestation,
    is_goal_eligible_mission,
    read_goal_condition,
    vp_goal_enabled,
)


class TestVpGoalEnabled:
    def test_unset_returns_false(self, monkeypatch):
        monkeypatch.delenv("UA_VP_GOAL_ENABLED", raising=False)
        assert vp_goal_enabled() is False

    def test_empty_returns_false(self, monkeypatch):
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", "")
        assert vp_goal_enabled() is False

    def test_zero_returns_false(self, monkeypatch):
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", "0")
        assert vp_goal_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "True", "YES", "on", "On"])
    def test_truthy_returns_true(self, monkeypatch, value):
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", value)
        assert vp_goal_enabled() is True


class TestIsGoalEligibleMission:
    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", "1")

    def test_cody_with_eligible_source_kind_is_eligible(self):
        m = {"vp_id": "vp.coder.primary", "source_kind": "cody_demo_task"}
        assert is_goal_eligible_mission(m) is True

    def test_cody_with_eligible_mission_type_is_eligible(self):
        m = {"vp_id": "vp.coder.primary", "mission_type": "tutorial_build"}
        assert is_goal_eligible_mission(m) is True

    def test_cody_with_use_goal_loop_metadata_is_eligible(self):
        import json
        m = {
            "vp_id": "vp.coder.primary",
            "source_kind": "operator_dispatched",
            "payload_json": json.dumps({"metadata": {"use_goal_loop": True}}),
        }
        assert is_goal_eligible_mission(m) is True

    def test_cody_with_ineligible_source_kind_is_not_eligible(self):
        m = {"vp_id": "vp.coder.primary", "source_kind": "proactive_codie"}
        assert is_goal_eligible_mission(m) is False

    def test_atlas_is_never_eligible_even_for_eligible_source(self):
        m = {"vp_id": "vp.general.primary", "source_kind": "cody_demo_task"}
        assert is_goal_eligible_mission(m) is False

    def test_empty_mission_is_not_eligible(self):
        assert is_goal_eligible_mission({}) is False

    def test_feature_flag_off_blocks_source_kind_path(self, monkeypatch):
        """Flag-off still gates the global default-on (eligible source_kind) path."""
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", "0")
        m = {"vp_id": "vp.coder.primary", "source_kind": "cody_demo_task"}
        assert is_goal_eligible_mission(m) is False

    def test_use_goal_loop_override_bypasses_feature_flag(self, monkeypatch):
        """Explicit per-task opt-in via metadata.use_goal_loop=True must
        activate /goal even when UA_VP_GOAL_ENABLED is OFF (prod default).
        The dashboard's Dispatch Mission UI uses this flag as the per-task
        opt-in switch — gating it behind the global flag makes the UI dead
        code in prod.
        """
        import json
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", "0")
        m = {
            "vp_id": "vp.coder.primary",
            "source_kind": "operator_dispatched",
            "payload_json": json.dumps({"metadata": {"use_goal_loop": True}}),
        }
        assert is_goal_eligible_mission(m) is True

    def test_use_goal_loop_override_still_blocks_atlas(self, monkeypatch):
        """Override does NOT escape the Cody-only rule."""
        import json
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", "0")
        m = {
            "vp_id": "vp.general.primary",
            "source_kind": "operator_dispatched",
            "payload_json": json.dumps({"metadata": {"use_goal_loop": True}}),
        }
        assert is_goal_eligible_mission(m) is False

    def test_all_eligible_source_kinds_documented_in_constant(self):
        assert "cody_demo_task" in GOAL_ELIGIBLE_SOURCE_KINDS
        assert "cody_scaffold_request" in GOAL_ELIGIBLE_SOURCE_KINDS
        assert "tutorial_build" in GOAL_ELIGIBLE_SOURCE_KINDS


class TestBuildSelfBriefingPrompt:
    def test_includes_objective_verbatim(self, tmp_path):
        text = build_self_briefing_prompt(
            workspace_dir=tmp_path,
            objective="fix CSI drift",
            is_goal_eligible=False,
        )
        assert "fix CSI drift" in text

    def test_includes_workspace_path(self, tmp_path):
        text = build_self_briefing_prompt(
            workspace_dir=tmp_path,
            objective="any",
            is_goal_eligible=False,
        )
        assert str(tmp_path) in text

    def test_non_goal_eligible_skips_acceptance_requirement(self, tmp_path):
        text = build_self_briefing_prompt(
            workspace_dir=tmp_path,
            objective="any",
            is_goal_eligible=False,
        )
        assert "BRIEF.md" in text
        # ACCEPTANCE/goal_condition should be noted as skipped, not required.
        assert "skip ACCEPTANCE.md" in text or "is NOT /goal-eligible" in text

    def test_goal_eligible_requires_all_three_artifacts(self, tmp_path):
        text = build_self_briefing_prompt(
            workspace_dir=tmp_path,
            objective="any",
            is_goal_eligible=True,
        )
        assert "BRIEF.md" in text
        assert "ACCEPTANCE.md" in text
        assert "goal_condition.txt" in text
        assert "IS /goal-eligible" in text

    def test_mentions_self_brief_and_attest_skill_by_name(self, tmp_path):
        text = build_self_briefing_prompt(
            workspace_dir=tmp_path,
            objective="any",
            is_goal_eligible=False,
        )
        assert "self-brief-and-attest" in text

    def test_mentions_completion_md_requirement(self, tmp_path):
        text = build_self_briefing_prompt(
            workspace_dir=tmp_path,
            objective="any",
            is_goal_eligible=False,
        )
        assert "COMPLETION.md" in text
        assert "missing_completion_attestation" in text


class TestReadGoalCondition:
    def test_returns_none_when_missing(self, tmp_path):
        assert read_goal_condition(tmp_path) is None

    def test_returns_stripped_text_when_present(self, tmp_path):
        (tmp_path / "goal_condition.txt").write_text("  the condition  \n")
        assert read_goal_condition(tmp_path) == "the condition"

    def test_returns_none_when_empty(self, tmp_path):
        (tmp_path / "goal_condition.txt").write_text("   \n")
        assert read_goal_condition(tmp_path) is None

    def test_returns_none_when_too_long(self, tmp_path):
        (tmp_path / "goal_condition.txt").write_text("x" * (GOAL_CONDITION_MAX_CHARS + 1))
        assert read_goal_condition(tmp_path) is None

    def test_returns_text_at_max_chars(self, tmp_path):
        body = "x" * GOAL_CONDITION_MAX_CHARS
        (tmp_path / "goal_condition.txt").write_text(body)
        assert read_goal_condition(tmp_path) == body


class TestCheckCompletionAttestation:
    def test_missing_returns_false(self, tmp_path):
        ok, reason = check_completion_attestation(tmp_path)
        assert ok is False
        assert "COMPLETION.md was not written" in reason

    def test_present_and_nonempty_returns_true(self, tmp_path):
        (tmp_path / "COMPLETION.md").write_text("# COMPLETION\n\nDid the work.\n")
        ok, reason = check_completion_attestation(tmp_path)
        assert ok is True
        assert reason is None

    def test_empty_returns_false(self, tmp_path):
        (tmp_path / "COMPLETION.md").write_text("   \n")
        ok, reason = check_completion_attestation(tmp_path)
        assert ok is False
        assert "empty" in reason

    # PR #492 — fallback_dirs (Cody's actual cwd when BRIEF redirects
    # work to /tmp). Worker_loop populates this from
    # outcome.payload.cli_workspace_dir.

    def test_fallback_dir_has_completion_returns_true(self, tmp_path):
        """Canonical workspace empty but fallback (cli cwd) has it."""
        canonical = tmp_path / "canonical"
        fallback = tmp_path / "tmp_cwd"
        canonical.mkdir()
        fallback.mkdir()
        (fallback / "COMPLETION.md").write_text("# done")
        ok, reason = check_completion_attestation(canonical, fallback_dirs=[fallback])
        assert ok is True
        assert reason is None

    def test_canonical_preferred_over_fallback(self, tmp_path):
        """If canonical has COMPLETION.md, fallback isn't even checked."""
        canonical = tmp_path / "canonical"
        fallback = tmp_path / "tmp_cwd"
        canonical.mkdir()
        fallback.mkdir()
        (canonical / "COMPLETION.md").write_text("# real")
        # Fallback intentionally has bad content — should never be read.
        (fallback / "COMPLETION.md").write_text("")
        ok, reason = check_completion_attestation(canonical, fallback_dirs=[fallback])
        assert ok is True
        assert reason is None

    def test_neither_has_completion_returns_false(self, tmp_path):
        canonical = tmp_path / "canonical"
        fallback = tmp_path / "tmp_cwd"
        canonical.mkdir()
        fallback.mkdir()
        ok, reason = check_completion_attestation(canonical, fallback_dirs=[fallback])
        assert ok is False
        assert "not written" in reason

    def test_empty_fallback_dirs_behaves_like_no_fallback(self, tmp_path):
        """``fallback_dirs=None`` and ``fallback_dirs=[]`` are both no-ops."""
        ok, _ = check_completion_attestation(tmp_path, fallback_dirs=[])
        assert ok is False
        ok, _ = check_completion_attestation(tmp_path, fallback_dirs=None)
        assert ok is False

    def test_fallback_dedup_does_not_double_check(self, tmp_path):
        """If a fallback path equals workspace_dir, it's deduplicated."""
        ok, reason = check_completion_attestation(tmp_path, fallback_dirs=[tmp_path])
        assert ok is False
        # Reason still mentions canonical, not a duplicate path message.
        assert "not written" in reason


class TestBuildCliPromptInjectsBriefingAndCompletion:
    """Smoke-test that _build_cli_prompt inserts the right sections when flag is on."""

    def test_flag_off_no_briefing_no_completion(self, tmp_path, monkeypatch):
        monkeypatch.delenv("UA_VP_GOAL_ENABLED", raising=False)
        from universal_agent.vp.clients.claude_cli_client import _build_cli_prompt

        text = _build_cli_prompt(
            objective="do the work",
            payload={},
            workspace_dir=tmp_path,
            skill_name="",
        )
        assert "Self-briefing" not in text
        assert "COMPLETION.md" not in text

    def test_flag_on_no_brief_yet_injects_briefing_directive(self, tmp_path, monkeypatch):
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", "1")
        from universal_agent.vp.clients.claude_cli_client import _build_cli_prompt

        text = _build_cli_prompt(
            objective="do the work",
            payload={},
            workspace_dir=tmp_path,
            skill_name="",
        )
        assert "Self-briefing (REQUIRED FIRST STEP)" in text
        assert "self-brief-and-attest" in text
        assert "COMPLETION.md" in text

    def test_flag_on_brief_exists_points_at_artifacts_not_re_brief(self, tmp_path, monkeypatch):
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", "1")
        from universal_agent.vp.clients.claude_cli_client import _build_cli_prompt

        (tmp_path / "BRIEF.md").write_text("# BRIEF\n\nMy interpretation.\n")
        text = _build_cli_prompt(
            objective="do the work",
            payload={},
            workspace_dir=tmp_path,
            skill_name="",
        )
        assert "Self-briefing artifacts (from prior turn)" in text
        assert "Self-briefing (REQUIRED FIRST STEP)" not in text
        assert "COMPLETION.md" in text
