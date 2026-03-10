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


def test_park_csi_items_not_matching_event_types() -> None:
    conn = _conn()
    try:
        task_hub.upsert_csi_item(
            conn,
            event_id="evt-keep",
            event_type="opportunity_bundle_ready",
            source="csi_analytics",
            title="Keep me",
            message="opportunity event",
            project_key="mission",
            labels=["CSI", "agent-ready"],
            priority=3,
            incident_key="incident-keep",
            must_complete=False,
            mirror_status="internal_only",
        )
        task_hub.upsert_csi_item(
            conn,
            event_id="evt-park",
            event_type="hourly_token_usage_report",
            source="csi_analytics",
            title="Park me",
            message="routine event",
            project_key="csi",
            labels=["CSI", "agent-ready"],
            priority=2,
            incident_key="incident-park",
            must_complete=False,
            mirror_status="internal_only",
        )

        result = task_hub.park_csi_items_not_matching_event_types(
            conn,
            allowed_event_types={"opportunity_bundle_ready"},
            park_reason="unit_test_policy",
        )

        assert result["parked"] == 1
        keep = task_hub.get_item(conn, "csi:incident-keep")
        park = task_hub.get_item(conn, "csi:incident-park")
        assert keep is not None and keep["status"] == task_hub.TASK_STATUS_OPEN
        assert park is not None and park["status"] == task_hub.TASK_STATUS_PARKED
        assert str((park.get("metadata") or {}).get("auto_parked_reason")) == "unit_test_policy"
    finally:
        conn.close()


def test_system_schedule_review_task_is_dispatch_eligible() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "scmd:schedule-heartbeat",
                "source_kind": "system_command",
                "source_ref": "ops",
                "title": "Change heartbeat schedule",
                "description": "Command block instruction",
                "project_key": "immediate",
                "priority": 2,
                "labels": ["agent-ready", "schedule-command"],
                "status": task_hub.TASK_STATUS_REVIEW,
                "must_complete": False,
                "agent_ready": True,
                "metadata": {
                    "intent": "schedule_task",
                    "schedule_text": "every ten minutes",
                    "repeat_schedule": True,
                },
            },
        )
        task_hub.rebuild_dispatch_queue(conn)
        queue = task_hub.get_dispatch_queue(conn, limit=20)
        rows = [row for row in (queue.get("items") or []) if row.get("task_id") == "scmd:schedule-heartbeat"]
        assert len(rows) == 1
        assert rows[0]["eligible"] is True
        assert rows[0]["skip_reason"] is None
    finally:
        conn.close()


def test_dispatch_queue_reports_below_threshold_reason() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:below-threshold",
                "source_kind": "internal",
                "source_ref": "ops",
                "title": "Low score task",
                "description": "Should be deferred",
                "project_key": "immediate",
                "priority": 1,
                "labels": ["agent-ready"],
                "status": task_hub.TASK_STATUS_OPEN,
                "must_complete": False,
                "agent_ready": True,
            },
        )
        task_hub.rebuild_dispatch_queue(conn)
        queue = task_hub.get_dispatch_queue(conn, limit=20)
        rows = [row for row in (queue.get("items") or []) if row.get("task_id") == "task:below-threshold"]
        assert len(rows) == 1
        assert rows[0]["eligible"] is False
        assert rows[0]["skip_reason"] == "below_threshold"
    finally:
        conn.close()
