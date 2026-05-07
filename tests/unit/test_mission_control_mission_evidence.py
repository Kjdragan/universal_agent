from __future__ import annotations

import sqlite3

from universal_agent import task_hub
from universal_agent.services import mission_control_chief_of_staff as cos
from universal_agent.services import mission_control_tier1 as tier1
from universal_agent.services.mission_control_db import open_store


def _activity_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_events (
            id TEXT PRIMARY KEY,
            event_class TEXT NOT NULL DEFAULT 'notification',
            source_domain TEXT NOT NULL DEFAULT 'task_hub',
            kind TEXT NOT NULL DEFAULT 'noop',
            title TEXT NOT NULL DEFAULT 'noop',
            summary TEXT NOT NULL DEFAULT '',
            full_message TEXT NOT NULL DEFAULT '',
            severity TEXT NOT NULL DEFAULT 'info',
            status TEXT NOT NULL DEFAULT 'new',
            requires_action INTEGER NOT NULL DEFAULT 0,
            session_id TEXT,
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            entity_ref_json TEXT NOT NULL DEFAULT '{}',
            actions_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    return conn


def test_tier1_evidence_includes_mission_summaries(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_TASK_HUB_MISSIONS_ENABLED", "1")
    activity_conn = _activity_conn()
    try:
        task_hub.create_mission_envelope(
            activity_conn,
            task_id="mission:tier1",
            title="Tier1 mission",
            mission_plan={"phases": [{"phase_id": "analysis", "subtask_role": "analysis", "title": "Analyze"}]},
        )
        mc_path = tmp_path / "mc.db"
        mc_conn = open_store(mc_path)
        try:
          evidence = tier1.collect_tier1_evidence(activity_conn, mc_conn, task_limit=10, completed_task_limit=10, event_limit=10)
        finally:
          mc_conn.close()
        assert len(evidence["mission_summaries"]) == 1
        assert evidence["mission_summaries"][0]["workstream_id"] == "mission:tier1"
    finally:
        activity_conn.close()


def test_chief_of_staff_evidence_includes_mission_summaries(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_TASK_HUB_MISSIONS_ENABLED", "1")
    db_path = tmp_path / "activity.db"
    monkeypatch.setattr(cos, "get_activity_db_path", lambda: str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        task_hub.ensure_schema(conn)
        task_hub.create_mission_envelope(
            conn,
            task_id="mission:cos",
            title="COS mission",
            mission_plan={"phases": [{"phase_id": "analysis", "subtask_role": "analysis", "title": "Analyze"}]},
        )
        evidence = cos.collect_task_hub_evidence(limit=10, completed_limit=10)
        assert len(evidence["mission_summaries"]) == 1
        assert evidence["mission_summaries"][0]["workstream_id"] == "mission:cos"
    finally:
        conn.close()
