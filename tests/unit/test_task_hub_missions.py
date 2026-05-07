from __future__ import annotations

import sqlite3

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def test_create_mission_envelope_spawns_first_child(monkeypatch):
    monkeypatch.setenv("UA_TASK_HUB_MISSIONS_ENABLED", "1")
    conn = _conn()
    try:
        mission = task_hub.create_mission_envelope(
            conn,
            task_id="mission:video-demo",
            title="Video demo mission",
            description="Analyze then build demo",
            mission_plan={
                "mission_title": "Video demo mission",
                "phases": [
                    {"phase_id": "analysis", "subtask_role": "analysis", "title": "Analyze video", "workflow_kind": "research_report_email"},
                    {"phase_id": "demo_build", "subtask_role": "demo_build", "title": "Build demo", "workflow_kind": "code_change"},
                ],
            },
        )
        assert mission["task_id"] == "mission:video-demo"
        assert mission["source_kind"] == task_hub.MISSION_ENVELOPE_SOURCE_KIND
        assert mission["workstream_id"] == "mission:video-demo"

        children = task_hub.list_workstream_tasks(conn, "mission:video-demo", include_parent=False)
        assert len(children) == 1
        assert children[0]["subtask_role"] == "analysis"
        assert children[0]["workstream_id"] == "mission:video-demo"
    finally:
        conn.close()


def test_complete_child_spawns_next_child(monkeypatch):
    monkeypatch.setenv("UA_TASK_HUB_MISSIONS_ENABLED", "1")
    conn = _conn()
    try:
        task_hub.create_mission_envelope(
            conn,
            task_id="mission:seq",
            title="Sequential mission",
            mission_plan={
                "phases": [
                    {"phase_id": "analysis", "subtask_role": "analysis", "title": "Analyze"},
                    {"phase_id": "demo_build", "subtask_role": "demo_build", "title": "Build"},
                ]
            },
        )
        children = task_hub.list_workstream_tasks(conn, "mission:seq", include_parent=False)
        assert [child["subtask_role"] for child in children] == ["analysis"]

        task_hub.perform_task_action(
            conn,
            task_id=children[0]["task_id"],
            action="complete",
            agent_id="tester",
        )

        children = task_hub.list_workstream_tasks(conn, "mission:seq", include_parent=False)
        roles = [child["subtask_role"] for child in children]
        assert roles == ["analysis", "demo_build"]
    finally:
        conn.close()


def test_decompose_task_inherits_workstream_id(monkeypatch):
    monkeypatch.setenv("UA_TASK_HUB_MISSIONS_ENABLED", "1")
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "parent-task",
                "source_kind": task_hub.MISSION_PHASE_SOURCE_KIND,
                "title": "Parent",
                "description": "",
                "project_key": "immediate",
                "priority": 2,
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
                "workstream_id": "mission:xyz",
            },
        )
        children = task_hub.decompose_task(
            conn,
            parent_task_id="parent-task",
            subtasks=[{"title": "Sub A"}, {"title": "Sub B"}],
        )
        assert all(child["workstream_id"] == "mission:xyz" for child in children)
    finally:
        conn.close()


def test_list_agent_queue_excludes_mission_envelopes(monkeypatch):
    monkeypatch.setenv("UA_TASK_HUB_MISSIONS_ENABLED", "1")
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "mission:root",
                "source_kind": task_hub.MISSION_ENVELOPE_SOURCE_KIND,
                "title": "Mission Root",
                "description": "",
                "project_key": "immediate",
                "priority": 2,
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": False,
                "workstream_id": "mission:root",
            },
        )
        task_hub.upsert_item(
            conn,
            {
                "task_id": "mission:root:phase:1",
                "source_kind": task_hub.MISSION_PHASE_SOURCE_KIND,
                "title": "Phase 1",
                "description": "",
                "project_key": "immediate",
                "priority": 2,
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
                "workstream_id": "mission:root",
                "parent_task_id": "mission:root",
                "subtask_role": "analysis",
            },
        )
        queue = task_hub.list_agent_queue(conn, include_not_ready=True)
        ids = [item["task_id"] for item in queue["items"]]
        assert "mission:root" not in ids
        assert "mission:root:phase:1" in ids
    finally:
        conn.close()


def test_build_task_mission_summary_rolls_up_counts(monkeypatch):
    monkeypatch.setenv("UA_TASK_HUB_MISSIONS_ENABLED", "1")
    conn = _conn()
    try:
        task_hub.create_mission_envelope(
            conn,
            task_id="mission:summary",
            title="Summary mission",
            mission_plan={
                "phases": [
                    {"phase_id": "analysis", "subtask_role": "analysis", "title": "Analyze"},
                    {"phase_id": "build", "subtask_role": "demo_build", "title": "Build"},
                ]
            },
        )
        children = task_hub.list_workstream_tasks(conn, "mission:summary", include_parent=False)
        task_hub.perform_task_action(conn, task_id=children[0]["task_id"], action="complete", agent_id="tester")
        summary = task_hub.build_workstream_summary(conn, "mission:summary")
        assert summary is not None
        assert summary["workstream_id"] == "mission:summary"
        assert summary["child_counts"]["total"] == 2
        assert summary["child_counts"]["completed"] == 1
        assert summary["current_phase_id"] in {"build", "demo_build"}
    finally:
        conn.close()
