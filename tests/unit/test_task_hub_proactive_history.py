from __future__ import annotations

from pathlib import Path
import sqlite3

from universal_agent import task_hub
from universal_agent.services.proactive_work_recap import get_recap_for_task


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
