from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def test_upsert_open_refresh_does_not_clobber_in_progress_state() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "csi:test-incident",
                "source_kind": "csi",
                "source_ref": "evt-1",
                "title": "CSI Incident",
                "description": "Initial dispatch",
                "project_key": "immediate",
                "priority": 4,
                "labels": ["agent-ready", "must-complete"],
                "status": task_hub.TASK_STATUS_IN_PROGRESS,
                "must_complete": True,
                "agent_ready": True,
                "incident_key": "test-incident",
                "seizure_state": "seized",
            },
        )

        refreshed = task_hub.upsert_csi_item(
            conn,
            event_id="evt-2",
            event_type="csi_update",
            source="csi_analytics",
            title="CSI Incident",
            message="Latest update payload",
            project_key="immediate",
            labels=["agent-ready", "must-complete"],
            priority=4,
            incident_key="test-incident",
            must_complete=True,
            mirror_status="internal",
        )

        assert refreshed["status"] == task_hub.TASK_STATUS_IN_PROGRESS
        assert refreshed["seizure_state"] == "seized"
    finally:
        conn.close()


def test_finalize_assignments_reopens_in_progress_items() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:dispatch-1",
                "source_kind": "internal",
                "title": "Dispatch Candidate",
                "description": "Needs handling",
                "project_key": "immediate",
                "priority": 4,
                "labels": ["agent-ready", "must-complete"],
                "status": task_hub.TASK_STATUS_OPEN,
                "must_complete": True,
                "agent_ready": True,
            },
        )

        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="heartbeat:s1")
        assert len(claimed) == 1
        assignment_id = str(claimed[0]["assignment_id"])

        result = task_hub.finalize_assignments(
            conn,
            assignment_ids=[assignment_id],
            state="completed",
            result_summary="heartbeat_run_finished",
            reopen_in_progress=True,
        )

        assert result == {"finalized": 1, "reopened": 1}
        item = task_hub.get_item(conn, "task:dispatch-1")
        assert item is not None
        assert item["status"] == task_hub.TASK_STATUS_OPEN
        assert item["seizure_state"] == "unseized"
    finally:
        conn.close()


def test_release_stale_assignments_abandons_old_heartbeat_claims() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:stale-1",
                "source_kind": "internal",
                "title": "Stale dispatch",
                "description": "Old heartbeat claim",
                "project_key": "immediate",
                "priority": 4,
                "labels": ["agent-ready", "must-complete"],
                "status": task_hub.TASK_STATUS_OPEN,
                "must_complete": True,
                "agent_ready": True,
            },
        )

        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="heartbeat:s2")
        assert len(claimed) == 1
        assignment_id = str(claimed[0]["assignment_id"])

        old_started = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        conn.execute(
            "UPDATE task_hub_assignments SET started_at=? WHERE assignment_id=?",
            (old_started, assignment_id),
        )
        conn.commit()

        result = task_hub.release_stale_assignments(
            conn,
            agent_id_prefix="heartbeat:",
            stale_after_seconds=300,
        )

        assert result == {"stale_detected": 1, "finalized": 1, "reopened": 1}
        row = conn.execute(
            "SELECT state, ended_at FROM task_hub_assignments WHERE assignment_id=?",
            (assignment_id,),
        ).fetchone()
        assert row is not None
        assert str(row["state"]) == "abandoned"
        assert str(row["ended_at"] or "").strip() != ""
    finally:
        conn.close()
