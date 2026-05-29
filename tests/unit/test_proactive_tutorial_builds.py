from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services import proactive_tutorial_builds as ptb
from universal_agent.services.proactive_tutorial_builds import (
    is_video_buildable_with_judge,
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


def test_no_summary_does_not_persist_terminal_verdict(tmp_path):
    """An empty transcript summary must NOT be cached as a terminal verdict.

    Regression for the ingestion->analysis race: the proactive sweep often sees
    a CSI video before its transcript summary has been analyzed. Caching a
    ``no_summary`` verdict there permanently locked the video out, because the
    cache-read short-circuited before the LLM judge on every later sweep — even
    after the summary was backfilled. The empty-summary path must skip WITHOUT
    caching so the video is re-judged once a summary exists.
    """
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        result = is_video_buildable_with_judge(
            conn,
            video_id="vid_race",
            title="Build an MCP server",
            channel_name="AI Builder",
            summary_text="   ",  # whitespace-only == no summary yet
        )
        assert result is False
        # Nothing terminal persisted -> the video re-judges on a later sweep.
        assert ptb._get_cached_judge_verdict(conn, "vid_race") is None
        row_count = conn.execute(
            "SELECT COUNT(*) FROM tutorial_build_judge WHERE video_id = ?",
            ("vid_race",),
        ).fetchone()[0]
        assert row_count == 0


def test_legacy_no_summary_cache_row_is_treated_as_miss(tmp_path):
    """A pre-existing ``no_summary`` verdict must not lock out the video.

    Covers the 534 production rows written before this fix: they physically
    exist in the cache, but the read path must treat ``method='no_summary'`` as
    a miss so those videos get re-judged now that their summaries are present.
    """
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        ptb._cache_judge_verdict(
            conn,
            video_id="vid_stuck",
            buildable=False,
            reasoning="no transcript summary available — cannot judge buildability",
            method="no_summary",
        )
        # The row exists physically...
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM tutorial_build_judge WHERE video_id = ?",
                ("vid_stuck",),
            ).fetchone()[0]
            == 1
        )
        # ...but is treated as a cache MISS so the video re-judges.
        assert ptb._get_cached_judge_verdict(conn, "vid_stuck") is None


def test_real_llm_verdict_is_still_cached_and_returned(tmp_path):
    """Regression: genuine verdicts (method != no_summary) are still honored."""
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        ptb._cache_judge_verdict(
            conn,
            video_id="vid_real",
            buildable=True,
            reasoning="clear, concrete MCP build steps",
            method="llm",
        )
        cached = ptb._get_cached_judge_verdict(conn, "vid_real")
        assert cached is not None
        assert cached["buildable"] is True
        assert cached["method"] == "llm"


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
