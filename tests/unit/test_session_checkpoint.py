import json
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


def test_session_checkpoint_writes_run_and_legacy_checkpoint_files(tmp_path):
    generator = SessionCheckpointGenerator(tmp_path)
    checkpoint = generator.generate_from_result(
        session_id="run_test",
        original_request="Do the thing.",
        result=SimpleNamespace(tool_calls=1, execution_time_seconds=1.0),
    )

    saved_path = generator.save(checkpoint)

    assert saved_path == tmp_path / "run_checkpoint.json"
    assert (tmp_path / "run_checkpoint.json").exists()
    assert (tmp_path / "run_checkpoint.md").exists()
    assert (tmp_path / "session_checkpoint.json").exists()
    assert (tmp_path / "session_checkpoint.md").exists()


def test_session_checkpoint_load_latest_reads_legacy_if_run_checkpoint_missing(tmp_path):
    generator = SessionCheckpointGenerator(tmp_path)
    legacy_payload = {
        "session_id": "session_legacy",
        "original_request": "Legacy checkpoint",
        "completed_tasks": ["a"],
    }
    (tmp_path / "session_checkpoint.json").write_text(
        json.dumps(legacy_payload),
        encoding="utf-8",
    )

    checkpoint = generator.load_latest()

    assert checkpoint is not None
    assert checkpoint.session_id == "session_legacy"
    assert checkpoint.original_request == "Legacy checkpoint"
