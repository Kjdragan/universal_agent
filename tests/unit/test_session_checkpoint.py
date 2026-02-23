from types import SimpleNamespace

from universal_agent.session_checkpoint import SessionCheckpointGenerator


def test_session_checkpoint_persists_goal_satisfaction_and_research_adherence(tmp_path):
    generator = SessionCheckpointGenerator(tmp_path)
    result = SimpleNamespace(
        tool_calls=9,
        execution_time_seconds=12.5,
        goal_satisfaction={
            "passed": True,
            "observed": {
                "research_pipeline_adherence": {
                    "required": True,
                    "passed": True,
                    "run_research_phase_called": True,
                    "pre_phase_workspace_scouting_calls": 0,
                }
            },
        },
    )

    checkpoint = generator.generate_from_result(
        session_id="session_test",
        original_request="Research and report.",
        result=result,
    )

    assert checkpoint.goal_satisfaction["passed"] is True
    adherence = checkpoint.goal_satisfaction["observed"]["research_pipeline_adherence"]
    assert adherence["required"] is True
    assert adherence["passed"] is True

    markdown = checkpoint.to_markdown()
    assert "### Goal Satisfaction" in markdown
    assert "Research pipeline adherence: passed" in markdown
    assert "run_research_phase called: true" in markdown
