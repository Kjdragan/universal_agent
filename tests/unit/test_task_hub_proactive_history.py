from __future__ import annotations

from pathlib import Path
import json
import sqlite3

from universal_agent import task_hub
from universal_agent.services import proactive_work_recap
from universal_agent.services.proactive_work_recap import get_recap_for_task, upsert_recap_for_task


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_list_proactive_work_tasks_includes_open_and_completed_pipeline_items(tmp_path):
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "proactive-open",
                "source_kind": "claude_code_kb_update",
                "source_ref": "x-post-1",
                "title": "Assess Claude Code release note",
                "description": "Produce a brief.",
                "project_key": "proactive",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
            },
        )
        task_hub.upsert_item(
            conn,
            {
                "task_id": "proactive-completed",
                "source_kind": "proactive_codie",
                "source_ref": "cleanup",
                "title": "CODIE cleanup",
                "description": "Open a PR.",
                "project_key": "proactive",
                "status": task_hub.TASK_STATUS_COMPLETED,
                "agent_ready": True,
            },
        )
        task_hub.upsert_item(
            conn,
            {
                "task_id": "manual-dashboard",
                "source_kind": "dashboard_quick_add",
                "title": "User-directed task",
                "description": "Not autonomous.",
                "project_key": "immediate",
                "status": task_hub.TASK_STATUS_COMPLETED,
            },
        )

        rows = task_hub.list_proactive_work_tasks(conn, limit=20)

    task_ids = {row["task_id"] for row in rows}
    assert "proactive-open" in task_ids
    assert "proactive-completed" in task_ids
    assert "manual-dashboard" not in task_ids


def test_terminal_proactive_action_stores_session_recap(tmp_path):
    db_path = tmp_path / "activity.db"
    workspace = tmp_path / "workspace"
    (workspace / "work_products").mkdir(parents=True)
    (workspace / "work_products" / "brief.md").write_text("# Brief\nUseful findings.\n", encoding="utf-8")
    (workspace / "transcript.md").write_text("Agent completed the proactive brief and saved it.\n", encoding="utf-8")
    with _connect(db_path) as conn:
        task = task_hub.upsert_item(
            conn,
            {
                "task_id": "proactive-recap",
                "source_kind": "proactive_codie",
                "source_ref": "cleanup",
                "title": "Create proactive cleanup brief",
                "description": "Write the first useful deliverable.",
                "project_key": "proactive",
                "status": task_hub.TASK_STATUS_IN_PROGRESS,
                "agent_ready": True,
            },
        )
        conn.execute(
            """
            INSERT INTO task_hub_assignments (
                assignment_id, task_id, agent_id, provider_session_id,
                workspace_dir, state, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "asg-proactive-recap",
                task["task_id"],
                "vp.coder.primary",
                "session-proactive-recap",
                str(workspace),
                "running",
                "2026-04-29T10:00:00+00:00",
            ),
        )
        conn.commit()

        task_hub.perform_task_action(
            conn,
            task_id=task["task_id"],
            action="complete",
            reason="Created the proactive cleanup brief.",
            agent_id="vp.coder.primary",
        )
        recap = get_recap_for_task(conn, task["task_id"])

    assert recap is not None
    assert recap["idea"] == "Create proactive cleanup brief"
    assert "Created the proactive cleanup brief" in recap["implemented"]
    assert "work_products/brief.md" in recap["implemented"]
    assert recap["evaluation_status"] == "session_evidence_evaluated"
    assert recap["recommended_next_action"]


def test_proactive_recap_uses_llm_when_enabled(monkeypatch, tmp_path):
    db_path = tmp_path / "activity.db"
    workspace = tmp_path / "workspace"
    (workspace / "work_products").mkdir(parents=True)
    (workspace / "work_products" / "analysis.md").write_text("analysis", encoding="utf-8")
    (workspace / "run.log").write_text("ran analysis and produced artifact", encoding="utf-8")

    def fake_llm_evaluator(*, task, assignment, action, reason, evidence):
        return {
            "evaluation_status": "llm_evaluated",
            "idea": "LLM idea",
            "implemented": "LLM implementation summary",
            "known_issues": "LLM known issues",
            "success_assessment": "LLM success assessment",
            "recommended_next_action": "LLM next action",
            "confidence": 0.91,
            "raw_model_output": {"evaluator": "llm_recap_v1", "model": "fake"},
        }

    monkeypatch.setenv("UA_PROACTIVE_RECAP_LLM_ENABLED", "1")
    monkeypatch.setattr(proactive_work_recap, "_call_llm_recap_evaluator", fake_llm_evaluator)

    with _connect(db_path) as conn:
        task = task_hub.upsert_item(
            conn,
            {
                "task_id": "proactive-llm-recap",
                "source_kind": "proactive_codie",
                "title": "Evaluate proactive artifact",
                "description": "Use the model evaluator.",
                "project_key": "proactive",
                "status": task_hub.TASK_STATUS_COMPLETED,
            },
        )
        conn.execute(
            """
            INSERT INTO task_hub_assignments (
                assignment_id, task_id, agent_id, provider_session_id,
                workspace_dir, state, started_at, ended_at, result_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "asg-llm-recap",
                task["task_id"],
                "vp.general.primary",
                "session-llm-recap",
                str(workspace),
                "completed",
                "2026-04-29T11:00:00+00:00",
                "2026-04-29T11:05:00+00:00",
                "Initial summary",
            ),
        )
        conn.commit()
        recap = upsert_recap_for_task(conn, task=task, action="complete", reason="done")

    assert recap is not None
    assert recap["evaluation_status"] == "llm_evaluated"
    assert recap["idea"] == "LLM idea"
    assert recap["confidence"] == 0.91


def test_proactive_recap_falls_back_when_llm_fails(monkeypatch, tmp_path):
    db_path = tmp_path / "activity.db"

    def failing_llm_evaluator(*, task, assignment, action, reason, evidence):
        raise RuntimeError("model unavailable")

    monkeypatch.setenv("UA_PROACTIVE_RECAP_LLM_ENABLED", "1")
    monkeypatch.setattr(proactive_work_recap, "_call_llm_recap_evaluator", failing_llm_evaluator)

    with _connect(db_path) as conn:
        task = task_hub.upsert_item(
            conn,
            {
                "task_id": "proactive-llm-fallback",
                "source_kind": "proactive_codie",
                "title": "Fallback recap",
                "description": "Fallback when model fails.",
                "project_key": "proactive",
                "status": task_hub.TASK_STATUS_COMPLETED,
            },
        )
        recap = upsert_recap_for_task(conn, task=task, action="complete", reason="finished")

    assert recap is not None
    assert recap["evaluation_status"] == "llm_failed_fallback"
    assert recap["raw_model_output"]["llm_error"] == "model unavailable"
    assert recap["idea"] == "Fallback recap"


def test_feedback_can_create_proactive_continuation_task(tmp_path):
    db_path = tmp_path / "activity.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with _connect(db_path) as conn:
        task = task_hub.upsert_item(
            conn,
            {
                "task_id": "proactive-parent",
                "source_kind": "proactive_codie",
                "title": "Original proactive work",
                "description": "Initial autonomous output.",
                "project_key": "proactive",
                "status": task_hub.TASK_STATUS_COMPLETED,
                "priority": 3,
            },
        )
        conn.execute(
            """
            INSERT INTO task_hub_assignments (
                assignment_id, task_id, agent_id, provider_session_id,
                workspace_dir, state, started_at, ended_at, result_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "asg-parent",
                task["task_id"],
                "vp.general.primary",
                "session-parent",
                str(workspace),
                "completed",
                "2026-04-29T12:00:00+00:00",
                "2026-04-29T12:30:00+00:00",
                "Finished first pass.",
            ),
        )
        conn.commit()

        continuation = task_hub.create_proactive_feedback_continuation(
            conn,
            parent_task_id=task["task_id"],
            feedback_tags=["continue_work"],
            feedback_text="Please continue this with the next useful step.",
            actor="kevin",
        )
        rows = task_hub.list_proactive_work_tasks(conn, limit=20)

    assert continuation is not None
    assert continuation["source_kind"] == "proactive_feedback_continuation"
    assert continuation["parent_task_id"] == "proactive-parent"
    assert continuation["agent_ready"] is True
    assert "Previous workspace" in continuation["description"]
    assert str(workspace) in continuation["description"]
    assert continuation["task_id"] in {row["task_id"] for row in rows}


def test_feedback_without_continuation_signal_does_not_create_task(tmp_path):
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        task = task_hub.upsert_item(
            conn,
            {
                "task_id": "proactive-no-continuation",
                "source_kind": "proactive_codie",
                "title": "No continuation",
                "description": "Initial autonomous output.",
                "project_key": "proactive",
                "status": task_hub.TASK_STATUS_COMPLETED,
            },
        )
        continuation = task_hub.create_proactive_feedback_continuation(
            conn,
            parent_task_id=task["task_id"],
            feedback_tags=["successful"],
            feedback_text="This was useful.",
            actor="kevin",
        )

    assert continuation is None


def test_record_task_feedback_preserves_sentiment(tmp_path):
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        task = task_hub.upsert_item(
            conn,
            {
                "task_id": "proactive-feedback-sentiment",
                "source_kind": "proactive_codie",
                "title": "Sentiment feedback",
                "description": "Store sentiment.",
                "project_key": "proactive",
                "status": task_hub.TASK_STATUS_COMPLETED,
            },
        )
        updated = task_hub.record_task_feedback(
            conn,
            task_id=task["task_id"],
            feedback_tags=["helpful"],
            feedback_text="Useful.",
            sentiment="positive",
            actor="kevin",
        )

    feedback = json.loads(updated["feedback_json"])
    assert feedback["sentiment"] == "positive"
    assert feedback["actor"] == "kevin"
