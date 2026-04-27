from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services.proactive_tutorial_builds import (
    queue_tutorial_build_task,
    register_tutorial_bootstrap_job_artifact,
    register_tutorial_build_artifact,
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_queue_tutorial_build_task_requires_private_repo_policy(tmp_path):
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        seed = register_tutorial_build_artifact(
            conn,
            video_id="seed",
            title="Seed tutorial preference",
            artifact_path="/tmp/seed",
        )
        from universal_agent.services.proactive_artifacts import record_feedback
        from universal_agent.services.proactive_preferences import (
            record_artifact_feedback_signal,
        )

        updated = record_feedback(conn, artifact_id=seed["artifact_id"], score=5, text="more tutorials")
        record_artifact_feedback_signal(conn, artifact=updated, score=5, text="more tutorials")
        result = queue_tutorial_build_task(
            conn,
            video_id="abc123",
            video_title="Build an MCP server",
            video_url="https://youtube.test/watch?v=abc123",
            channel_name="AI Builder",
            extraction_plan={"language": "python", "implementation_steps": [{"step_number": 1, "description": "Create server"}]},
        )
        task = task_hub.get_item(conn, result["task"]["task_id"])

    assert task is not None
    assert task["source_kind"] == "tutorial_build"
    assert task["agent_ready"] is True
    assert "private by default" in task["description"].lower()
    assert "public publication is not allowed" in task["description"].lower()
    assert task["metadata"]["repo_visibility"] == "private"
    assert task["metadata"]["public_publication_allowed"] is False
    assert "Preference context:" in task["description"]
    assert result["artifact"]["artifact_type"] == "tutorial_build_task"


def test_register_tutorial_build_accepts_private_repo_or_local_fallback(tmp_path):
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        repo_artifact = register_tutorial_build_artifact(
            conn,
            video_id="abc123",
            title="Private MCP server demo",
            repo_url="https://github.com/Kjdragan/private-mcp-demo",
            run_commands="uv run python server.py",
            tests="uv run pytest -q",
        )
        local_artifact = register_tutorial_build_artifact(
            conn,
            video_id="def456",
            title="Local fallback demo",
            artifact_path="/tmp/tutorial-demo",
            status="github_unavailable",
        )

    assert repo_artifact["artifact_type"] == "tutorial_build"
    assert repo_artifact["metadata"]["repo_visibility"] == "private"
    assert repo_artifact["artifact_uri"].startswith("https://github.com/")
    assert local_artifact["artifact_path"] == "/tmp/tutorial-demo"
    assert local_artifact["metadata"]["build_status"] == "github_unavailable"


def test_register_tutorial_build_requires_output_location(tmp_path):
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        with pytest.raises(ValueError, match="repo_url or artifact_path is required"):
            register_tutorial_build_artifact(
                conn,
                video_id="abc123",
                title="Missing output",
            )


def test_register_tutorial_bootstrap_job_artifact_is_idempotent(tmp_path):
    db_path = tmp_path / "activity.db"
    job = {
        "job_id": "tbj_123",
        "status": "completed",
        "video_id": "abc123",
        "tutorial_title": "Private MCP server demo",
        "repo_dir": "/tmp/private-mcp-demo",
        "video_url": "https://youtube.test/watch?v=abc123",
    }
    with _connect(db_path) as conn:
        first = register_tutorial_bootstrap_job_artifact(conn, job)
        second = register_tutorial_bootstrap_job_artifact(conn, job)

    assert first is not None
    assert second is not None
    assert first["artifact_id"] == second["artifact_id"]
    assert first["artifact_path"] == "/tmp/private-mcp-demo"
