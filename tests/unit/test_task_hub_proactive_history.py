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
    parent_row = next(row for row in rows if row["task_id"] == "proactive-parent")
    continuation_row = next(row for row in rows if row["task_id"] == continuation["task_id"])
    assert parent_row["proactive_chain"]["child_count"] == 1
    assert parent_row["proactive_chain"]["continuation_count"] == 1
    assert parent_row["proactive_chain"]["children"][0]["task_id"] == continuation["task_id"]
    assert continuation_row["proactive_chain"]["is_continuation"] is True
    assert continuation_row["proactive_chain"]["parent"]["task_id"] == "proactive-parent"


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


def test_todo_prompt_surfaces_proactive_continuation_context(tmp_path):
    from universal_agent.services.todo_dispatch_service import build_todo_execution_prompt

    previous_workspace = str(tmp_path / "previous-workspace")
    prompt = build_todo_execution_prompt(
        claimed_items=[
            {
                "task_id": "proactive_cont:abc",
                "title": "Continue proactive work",
                "description": "Continue the prior proactive work in a fresh session.",
                "metadata": {
                    "proactive_continuation": {
                        "parent_task_id": "parent-task",
                        "previous_workspace_dir": previous_workspace,
                        "feedback_tags": ["continue_work"],
                        "feedback_text": "Please keep going.",
                        "prior_recap": {
                            "idea": "Original idea",
                            "implemented": "First implementation",
                            "known_issues": "Needs more work",
                            "recommended_next_action": "Continue",
                        },
                    }
                },
            }
        ],
        capacity_snapshot_data={},
        active_assignments=[],
        origin_label="test",
    )

    assert "== PROACTIVE CONTINUATION CONTEXT ==" in prompt
    assert f"previous_workspace_dir={previous_workspace}" in prompt
    assert "parent_task_id=parent-task" in prompt
    assert "prior_implemented=First implementation" in prompt
    assert "feedback_text=Please keep going." in prompt


def test_continuation_workspace_reference_uses_fresh_workspace_without_overwrite(tmp_path):
    from universal_agent.services.todo_dispatch_service import (
        CONTINUATION_CONTEXT_FILENAME,
        CONTINUATION_REFERENCE_DIRNAME,
        CONTINUATION_REFERENCE_LINKNAME,
        _attach_continuation_workspace_reference,
        build_todo_execution_prompt,
    )

    previous_workspace = tmp_path / "previous-workspace"
    current_workspace = tmp_path / "current-workspace"
    previous_workspace.mkdir()
    current_workspace.mkdir()
    (previous_workspace / "work_products.md").write_text("prior output", encoding="utf-8")
    item = {
        "task_id": "proactive_cont:workspace",
        "title": "Continue workspace task",
        "description": "Continue the prior work.",
        "metadata": {
            "proactive_continuation": {
                "parent_task_id": "parent-task",
                "previous_workspace_dir": str(previous_workspace),
                "feedback_tags": ["continue_work"],
                "feedback_text": "Keep developing this.",
            }
        },
    }

    context = _attach_continuation_workspace_reference(
        item,
        current_workspace_dir=str(current_workspace),
        run_id="run_test",
    )
    continuation = item["metadata"]["proactive_continuation"]
    reference_path = current_workspace / CONTINUATION_REFERENCE_DIRNAME / CONTINUATION_REFERENCE_LINKNAME

    assert context["mode"] == "referenced_existing_workspace"
    assert context["current_workspace_dir"] == str(current_workspace.resolve())
    assert context["previous_workspace_dir"] == str(previous_workspace.resolve())
    assert reference_path.is_symlink()
    assert reference_path.resolve() == previous_workspace.resolve()
    assert (current_workspace / CONTINUATION_CONTEXT_FILENAME).is_file()
    assert (previous_workspace / "work_products.md").read_text(encoding="utf-8") == "prior output"
    assert continuation["current_workspace_dir"] == str(current_workspace)
    assert continuation["workspace_reuse_mode"] == "referenced_existing_workspace"
    assert continuation["workspace_reference_path"] == str(reference_path)

    prompt = build_todo_execution_prompt(
        claimed_items=[item],
        capacity_snapshot_data={},
        active_assignments=[],
        origin_label="test",
    )

    assert f"current_workspace_dir={current_workspace}" in prompt
    assert f"previous_workspace_reference={reference_path}" in prompt
    assert "workspace_reuse_mode=referenced_existing_workspace" in prompt


def test_continuation_workspace_reference_writes_manifest_when_previous_missing(tmp_path):
    from universal_agent.services.todo_dispatch_service import (
        CONTINUATION_CONTEXT_FILENAME,
        _attach_continuation_workspace_reference,
    )

    previous_workspace = tmp_path / "missing-previous-workspace"
    current_workspace = tmp_path / "current-workspace"
    item = {
        "task_id": "proactive_cont:missing-workspace",
        "metadata": {
            "proactive_continuation": {
                "parent_task_id": "parent-task",
                "previous_workspace_dir": str(previous_workspace),
                "feedback_tags": ["continue_work"],
            }
        },
    }

    context = _attach_continuation_workspace_reference(
        item,
        current_workspace_dir=str(current_workspace),
        run_id="run_missing",
    )

    assert context["mode"] == "previous_workspace_missing"
    assert (current_workspace / CONTINUATION_CONTEXT_FILENAME).is_file()
    assert item["metadata"]["workspace_reuse_context"]["mode"] == "previous_workspace_missing"


def test_parked_proactive_fallback_recap_is_not_described_as_queued(tmp_path):
    from universal_agent import gateway_server

    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        task = task_hub.upsert_item(
            conn,
            {
                "task_id": "parked-proactive",
                "source_kind": "proactive_codie",
                "title": "Parked proactive work",
                "description": "Legacy proactive item.",
                "project_key": "proactive",
                "status": task_hub.TASK_STATUS_PARKED,
                "metadata": {
                    "dispatch": {
                        "last_disposition_reason": "legacy_pre_recap_pipeline",
                    }
                },
            },
        )

        recap = gateway_server._proactive_recap_for_task(conn, task)

    assert recap["status"] == "pending_llm_evaluation"
    assert "Parked" in recap["success_assessment"]
    assert "Queued for execution" not in recap["success_assessment"]
    assert recap["known_issues"] == "legacy_pre_recap_pipeline"


def test_proactive_history_maintenance_classifies_heartbeat_as_noise():
    from datetime import datetime, timezone

    from universal_agent.scripts.proactive_history_maintenance import classify_proactive_history_item

    classification = classify_proactive_history_item(
        {
            "task_id": "heartbeat-legacy",
            "source_kind": "heartbeat_remediation",
            "title": "Heartbeat Remediation: Noise",
            "description": "No action required.",
            "status": task_hub.TASK_STATUS_PARKED,
            "updated_at": "2026-04-24T10:00:00+00:00",
        },
        cutover=datetime(2026, 4, 30, tzinfo=timezone.utc),
        recap=None,
    )

    assert classification["action"] == "archive_legacy"
    assert classification["hidden_by_default"] is True
    assert "heartbeat_health_check_noise" in classification["reasons"]
    assert "pre_cutover_terminal_legacy" in classification["reasons"]


def test_proactive_history_maintenance_classifies_lifecycle_failure_for_investigation():
    from datetime import datetime, timezone

    from universal_agent.scripts.proactive_history_maintenance import classify_proactive_history_item

    classification = classify_proactive_history_item(
        {
            "task_id": "claude-legacy",
            "source_kind": "claude_code_kb_update",
            "title": "Analyze strategic Claude Code update",
            "description": "Process update.",
            "status": task_hub.TASK_STATUS_PARKED,
            "updated_at": "2026-04-23T10:00:00+00:00",
            "metadata": {"dispatch": {"last_disposition_reason": "stale_assignment_timeout:300s"}},
        },
        cutover=datetime(2026, 4, 30, tzinfo=timezone.utc),
        recap=None,
    )

    assert classification["action"] == "investigate"
    assert classification["hidden_by_default"] is True
    assert "historical_lifecycle_or_delivery_failure" in classification["reasons"]
