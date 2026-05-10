"""Unit tests for pure helper functions in urw/harness_helpers.py.

Covers: compact_agent_context, build_harness_context_injection,
create_harness_workspace. These are deterministic pure functions
with no external I/O (except mkdir which uses tmp_path fixtures).
"""

from pathlib import Path

from universal_agent.urw.harness_helpers import (
    build_harness_context_injection,
    compact_agent_context,
    create_harness_workspace,
)


class TestCompactAgentContext:
    def test_default_returns_hard_reset(self):
        result = compact_agent_context(client=None)
        assert result["keep_client"] is False
        assert "Hard reset" in result["notes"]

    def test_force_new_client_returns_hard_reset(self):
        result = compact_agent_context(client=None, force_new_client=True)
        assert result["keep_client"] is False
        assert "Explicit" in result["notes"]

    def test_returns_dict_with_required_keys(self):
        result = compact_agent_context(client="fake")
        assert "keep_client" in result
        assert "notes" in result

    def test_keep_client_always_false_in_current_strategy(self):
        """Both paths return keep_client=False per the hard-reset default."""
        for force in (True, False):
            result = compact_agent_context(client=None, force_new_client=force)
            assert result["keep_client"] is False


class TestBuildHarnessContextInjection:
    def test_minimal_single_phase(self):
        result = build_harness_context_injection(
            phase_num=1,
            total_phases=1,
            phase_title="Research",
            phase_instructions="Do the research.",
            prior_session_paths=[],
            expected_artifacts=["report.md"],
        )
        assert "Phase 1 of 1" in result
        assert "Research" in result
        assert "report.md" in result

    def test_multi_phase_shows_prior_paths(self):
        result = build_harness_context_injection(
            phase_num=2,
            total_phases=3,
            phase_title="Draft",
            phase_instructions="Write it up.",
            prior_session_paths=["/tmp/phase1", "/tmp/phase2_prev"],
            expected_artifacts=[],
        )
        assert "Phase 2 of 3" in result
        assert "/tmp/phase1" in result
        assert "/tmp/phase2_prev" in result

    def test_overall_goal_included(self):
        result = build_harness_context_injection(
            phase_num=1,
            total_phases=2,
            phase_title="Research",
            phase_instructions="Search.",
            prior_session_paths=[],
            expected_artifacts=[],
            overall_goal="Ship the feature.",
        )
        assert "Ship the feature." in result

    def test_no_overall_goal_omits_line(self):
        result = build_harness_context_injection(
            phase_num=1,
            total_phases=1,
            phase_title="Test",
            phase_instructions="Run tests.",
            prior_session_paths=[],
            expected_artifacts=[],
            overall_goal=None,
        )
        assert "Overall Project Goal" not in result

    def test_current_session_path_included(self):
        result = build_harness_context_injection(
            phase_num=1,
            total_phases=1,
            phase_title="Build",
            phase_instructions="Code.",
            prior_session_paths=[],
            expected_artifacts=[],
            current_session_path="/workspace/phase1",
        )
        assert "/workspace/phase1" in result
        assert "CURRENT WORKSPACE" in result

    def test_tasks_rendered(self):
        task = type("Task", (), {
            "name": "search",
            "description": "Find sources",
            "use_case": "Web search",
            "success_criteria": ["3 sources found"],
        })()
        result = build_harness_context_injection(
            phase_num=1,
            total_phases=1,
            phase_title="Search",
            phase_instructions="Go.",
            prior_session_paths=[],
            expected_artifacts=["sources.md"],
            tasks=[task],
        )
        assert "search" in result
        assert "Find sources" in result
        assert "Web search" in result
        assert "3 sources found" in result

    def test_empty_expected_artifacts_shows_success_message(self):
        result = build_harness_context_injection(
            phase_num=1,
            total_phases=1,
            phase_title="Think",
            phase_instructions="Contemplate.",
            prior_session_paths=[],
            expected_artifacts=[],
        )
        assert "Complete the phase successfully" in result


class TestCreateHarnessWorkspace:
    def test_creates_dir_with_auto_id(self, tmp_path):
        result = create_harness_workspace(tmp_path)
        assert result.exists()
        assert result.parent == tmp_path
        assert result.name.startswith("harness_")

    def test_creates_dir_with_explicit_id(self, tmp_path):
        result = create_harness_workspace(tmp_path, harness_id="my_test")
        assert result == tmp_path / "harness_my_test"
        assert result.exists()

    def test_idempotent_on_existing_dir(self, tmp_path):
        first = create_harness_workspace(tmp_path, harness_id="dup")
        second = create_harness_workspace(tmp_path, harness_id="dup")
        assert first == second
        assert first.exists()
