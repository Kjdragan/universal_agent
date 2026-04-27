from __future__ import annotations

from pathlib import Path
import sqlite3

from universal_agent import task_hub
from universal_agent.services.proactive_artifacts import get_artifact
from universal_agent.services.proactive_codie import (
    queue_cleanup_task,
    register_pr_artifact,
    register_pr_artifact_from_text,
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_queue_cleanup_task_creates_agent_ready_review_gated_task(tmp_path):
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        seed = register_pr_artifact(
            conn,
            pr_url="https://github.com/Kjdragan/universal_agent/pull/999",
            title="Seed CODIE preference",
        )
        from universal_agent.services.proactive_artifacts import record_feedback
        from universal_agent.services.proactive_preferences import (
            record_artifact_feedback_signal,
        )

        updated = record_feedback(conn, artifact_id=seed["artifact_id"], score=5, text="more cleanup")
        record_artifact_feedback_signal(conn, artifact=updated, score=5, text="more cleanup")
        result = queue_cleanup_task(
            conn,
            theme="reduce brittle routing heuristics",
            note="Focus on small diffs.",
            priority=3,
        )
        task = task_hub.get_item(conn, result["task"]["task_id"])
        artifact = get_artifact(conn, result["artifact"]["artifact_id"])

    assert task is not None
    assert task["source_kind"] == "proactive_codie"
    assert task["agent_ready"] is True
    assert task["trigger_type"] == "heartbeat_poll"
    assert "pull request targeting develop" in task["description"].lower()
    assert "do not merge" in task["description"].lower()
    assert "Preference context:" in task["description"]
    assert task["metadata"]["workflow_manifest"]["workflow_kind"] == "code_change"
    assert artifact is not None
    assert artifact["artifact_type"] == "codie_cleanup_task"


def test_register_pr_artifact_creates_review_candidate(tmp_path):
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        artifact = register_pr_artifact(
            conn,
            pr_url="https://github.com/Kjdragan/universal_agent/pull/123",
            title="Clean up routing prompt drift",
            summary="CODIE removed stale routing prompt fragments.",
            branch="codie/cleanup-routing",
            theme="routing cleanup",
            tests="uv run pytest tests/test_llm_classifier.py -q",
            risk="narrow",
        )

    assert artifact["artifact_type"] == "codie_pr"
    assert artifact["artifact_uri"].endswith("/pull/123")
    assert artifact["metadata"]["review_gate"] == "kevin_review_required_before_merge"
    assert "pull-request" in artifact["topic_tags"]


def test_register_pr_artifact_from_text_detects_github_pr_url(tmp_path):
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        artifact = register_pr_artifact_from_text(
            conn,
            text="Opened PR: https://github.com/Kjdragan/universal_agent/pull/456",
            title="CODIE cleanup PR",
            summary="PR ready for review.",
            theme="cleanup",
        )

    assert artifact is not None
    assert artifact["artifact_uri"].endswith("/pull/456")
    assert artifact["metadata"]["theme"] == "cleanup"
