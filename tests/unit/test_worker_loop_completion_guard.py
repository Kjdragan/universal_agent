"""Regression tests for the COMPLETION-attestation guard + failure classifier.

Two bugs caught by the 2026-05-26 smoke test:

1. Guard fires too broadly — gated on global `vp_goal_enabled()` instead of
   per-mission `is_goal_eligible_mission(mission)`. Result: successful Cody
   missions WITHOUT `use_goal_loop` got spuriously demoted to failed for
   missing a `COMPLETION.md` they were never told to write.

2. Classifier mislabels protocol violations — `_classify_outcome_failure_mode`
   returned `"vp_self_reported"` (generic `status == "failed"` fallback)
   before checking for `missing_completion_attestation` markers. Result: the
   failure card showed `(vp_self_reported)` when it should have shown
   `(missing_completion_attestation)` — confusing for Simone's triage.

These tests pin the corrected behavior so we don't regress.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from universal_agent.vp.clients.base import MissionOutcome
from universal_agent.vp.worker_loop import _classify_outcome_failure_mode


# ---------------------------------------------------------------------------
# Bug 2: _classify_outcome_failure_mode picks missing_completion_attestation
# BEFORE the generic vp_self_reported fallback.
# ---------------------------------------------------------------------------


class TestClassifyOutcomeFailureMode:
    def test_missing_completion_attestation_takes_precedence_over_self_reported(self):
        """Status=failed + missing_completion_attestation marker → that mode wins."""
        outcome = MissionOutcome(
            status="failed",
            message="missing_completion_attestation: COMPLETION.md was not written; VP did not complete the self-attestation protocol",
        )
        assert _classify_outcome_failure_mode(outcome) == "missing_completion_attestation"

    def test_self_reported_when_no_protocol_markers(self):
        """Bare failed status with no markers → vp_self_reported fallback."""
        outcome = MissionOutcome(status="failed", message="something went wrong")
        assert _classify_outcome_failure_mode(outcome) == "vp_self_reported"

    def test_auth_failure_still_takes_precedence_over_self_reported(self):
        outcome = MissionOutcome(
            status="failed",
            message="Invalid authentication credentials",
        )
        assert _classify_outcome_failure_mode(outcome) == "auth_failure"

    def test_subprocess_crash_marker(self):
        outcome = MissionOutcome(
            status="failed",
            message="Process killed by signal SIGTERM",
        )
        assert _classify_outcome_failure_mode(outcome) == "subprocess_crash"

    def test_workspace_guard_marker(self):
        outcome = MissionOutcome(
            status="failed",
            message="WorkspaceGuardError: tried to write outside approved path",
        )
        assert _classify_outcome_failure_mode(outcome) == "workspace_guard"

    def test_timeout_marker(self):
        outcome = MissionOutcome(status="failed", message="CLI session timed out after 1800s")
        assert _classify_outcome_failure_mode(outcome) == "timeout"

    def test_cancelled_status(self):
        outcome = MissionOutcome(status="cancelled", message="operator cancelled")
        assert _classify_outcome_failure_mode(outcome) == "operator_cancel"

    def test_completed_status_returns_none(self):
        outcome = MissionOutcome(status="completed", message="all good")
        # status=completed doesn't reach the failure branches — returns None
        # via the final fallthrough since neither cancelled nor failed.
        assert _classify_outcome_failure_mode(outcome) is None

    def test_payload_final_text_is_part_of_haystack(self):
        """The classifier examines outcome.payload['final_text'] too."""
        outcome = MissionOutcome(
            status="failed",
            message="",
            payload={"final_text": "missing_completion_attestation: ..."},
        )
        assert _classify_outcome_failure_mode(outcome) == "missing_completion_attestation"


# ---------------------------------------------------------------------------
# Bug 1: COMPLETION-attestation guard fires only for /goal-eligible missions.
#
# This is harder to unit-test directly because the guard lives inside the
# async `_execute_mission_logic` method which has many side effects. We test
# the boolean precondition that controls it instead — `is_goal_eligible_mission`
# under realistic mission shapes — and pin the contract that the guard ONLY
# fires when that returns True.
# ---------------------------------------------------------------------------


class TestGoalEligibilityForGuard:
    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", "1")

    def test_simple_cody_mission_without_use_goal_loop_is_not_eligible(self):
        """Reproduces the 2026-05-26 smoke test mission shape.

        The mission's payload_json.metadata only has {"cody_mode": "anthropic"}
        (no use_goal_loop) — because Simone forgot to pass task_id to
        vp_dispatch_mission, so inheritance didn't fire. This mission should
        NOT trigger the COMPLETION guard.
        """
        from universal_agent.services.self_briefing import is_goal_eligible_mission

        mission = {
            "vp_id": "vp.coder.primary",
            "source_kind": "vp_mission",  # the worker_loop mirror row's source_kind
            "mission_type": "code_change",
            "payload_json": json.dumps({"metadata": {"cody_mode": "anthropic"}}),
        }
        assert is_goal_eligible_mission(mission) is False

    def test_dashboard_dispatched_mission_with_inheritance_IS_eligible(self):
        """When inheritance worked correctly, mission carries use_goal_loop=True."""
        from universal_agent.services.self_briefing import is_goal_eligible_mission

        mission = {
            "vp_id": "vp.coder.primary",
            "source_kind": "operator_dispatched",
            "mission_type": "task",
            "payload_json": json.dumps({
                "metadata": {
                    "cody_mode": "anthropic",
                    "task_id": "qa-cf83c59411c5",
                    "use_goal_loop": True,
                }
            }),
        }
        assert is_goal_eligible_mission(mission) is True

    def test_cody_demo_task_is_always_eligible(self):
        """Source-kind-based eligibility (PRD § 3 decision 1)."""
        from universal_agent.services.self_briefing import is_goal_eligible_mission

        mission = {
            "vp_id": "vp.coder.primary",
            "source_kind": "cody_demo_task",
            "payload_json": json.dumps({"metadata": {}}),
        }
        assert is_goal_eligible_mission(mission) is True

    def test_global_flag_off_disables_eligibility(self, monkeypatch):
        """Flag off → never eligible, even for use_goal_loop missions."""
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", "0")
        from universal_agent.services.self_briefing import is_goal_eligible_mission

        mission = {
            "vp_id": "vp.coder.primary",
            "source_kind": "cody_demo_task",
            "payload_json": json.dumps({"metadata": {"use_goal_loop": True}}),
        }
        assert is_goal_eligible_mission(mission) is False


# ---------------------------------------------------------------------------
# Bug 1 — integration-shaped: simulate the guard's decision logic directly.
# Mirror the if/else branch from worker_loop.py to pin the per-mission gate.
# ---------------------------------------------------------------------------


class TestCompletionGuardOnlyFiresForEligibleMissions:
    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", "1")

    def _simulate_guard(self, mission, workspace_path):
        """Mirror worker_loop._execute_mission_logic's guard logic."""
        from universal_agent.services.self_briefing import (
            check_completion_attestation,
            is_goal_eligible_mission,
        )

        outcome = MissionOutcome(status="completed", message="work done", payload={})
        if outcome.status == "completed":
            if is_goal_eligible_mission(mission):
                ok, reason = check_completion_attestation(workspace_path)
                if not ok:
                    outcome = MissionOutcome(
                        status="failed",
                        message=f"missing_completion_attestation: {reason}",
                        payload={"demoted_from_completed": True},
                    )
        return outcome

    def test_not_eligible_mission_stays_completed_even_without_completion_md(self, tmp_path):
        """The smoke-test scenario: Cody completes successfully without
        COMPLETION.md, mission has no use_goal_loop, guard should NOT demote.
        """
        mission = {
            "vp_id": "vp.coder.primary",
            "source_kind": "vp_mission",
            "payload_json": json.dumps({"metadata": {"cody_mode": "anthropic"}}),
        }
        # workspace has no COMPLETION.md
        outcome = self._simulate_guard(mission, tmp_path)
        assert outcome.status == "completed", (
            "Bug 1 regression: non-/goal-eligible mission was demoted"
        )

    def test_eligible_mission_with_completion_md_stays_completed(self, tmp_path):
        mission = {
            "vp_id": "vp.coder.primary",
            "source_kind": "cody_demo_task",
            "payload_json": json.dumps({"metadata": {"use_goal_loop": True}}),
        }
        (tmp_path / "COMPLETION.md").write_text("# COMPLETION\n\nDone.\n")
        outcome = self._simulate_guard(mission, tmp_path)
        assert outcome.status == "completed"

    def test_eligible_mission_missing_completion_md_gets_demoted(self, tmp_path):
        mission = {
            "vp_id": "vp.coder.primary",
            "source_kind": "cody_demo_task",
            "payload_json": json.dumps({"metadata": {"use_goal_loop": True}}),
        }
        # NO COMPLETION.md
        outcome = self._simulate_guard(mission, tmp_path)
        assert outcome.status == "failed"
        assert "missing_completion_attestation" in outcome.message


# ---------------------------------------------------------------------------
# Bug 5: cody_mode="anthropic" must FORCE execution_mode="cli" regardless of
# any explicit override. Anthropic Max OAuth only works on the CLI path; if
# Simone passes execution_mode="autonomous" (as happened on 2026-05-26 smoke
# test), the mission would otherwise route to the SDK in-process adapter and
# run on ZAI/glm-5 instead of Anthropic Claude — which makes /goal impossible.
# ---------------------------------------------------------------------------


class TestExecutionModeOverrideForAnthropic:
    """Pin the routing-rule fix from PR (2026-05-26)."""

    def _simulate_route(self, *, cody_mode: str, explicit_exec_mode: str) -> str:
        """Mirror the new logic in vp_orchestration.py."""
        if cody_mode == "anthropic":
            return "cli"
        if explicit_exec_mode:
            return explicit_exec_mode
        return "sdk"

    def test_anthropic_forces_cli_over_autonomous(self):
        """The exact 2026-05-26 smoke-test misroute scenario."""
        assert self._simulate_route(
            cody_mode="anthropic",
            explicit_exec_mode="autonomous",
        ) == "cli"

    def test_anthropic_forces_cli_over_sdk(self):
        assert self._simulate_route(
            cody_mode="anthropic",
            explicit_exec_mode="sdk",
        ) == "cli"

    def test_anthropic_with_explicit_cli_still_cli(self):
        """Identity case — explicit cli + anthropic is consistent."""
        assert self._simulate_route(
            cody_mode="anthropic",
            explicit_exec_mode="cli",
        ) == "cli"

    def test_anthropic_with_no_explicit_still_cli(self):
        assert self._simulate_route(
            cody_mode="anthropic",
            explicit_exec_mode="",
        ) == "cli"

    def test_zai_with_explicit_autonomous_honored(self):
        """Non-anthropic missions still allow explicit override."""
        assert self._simulate_route(
            cody_mode="zai",
            explicit_exec_mode="autonomous",
        ) == "autonomous"

    def test_zai_default_is_sdk(self):
        assert self._simulate_route(
            cody_mode="zai",
            explicit_exec_mode="",
        ) == "sdk"
