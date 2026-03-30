from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    conn.execute(
        """
        CREATE TABLE email_task_mappings (
            task_id TEXT PRIMARY KEY,
            email_sent_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def _insert_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    status: str,
    metadata_json: str = "{}",
    seizure_state: str = "unseized",
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO task_hub_items (
            task_id, source_kind, source_ref, title, description, project_key, priority,
            due_at, labels_json, status, must_complete, incident_key, workstream_id,
            subtask_role, parent_task_id, agent_ready, score, score_confidence, stale_state,
            seizure_state, mirror_status, metadata_json, created_at, updated_at, trigger_type,
            refinement_stage, refinement_history_json, completion_token
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            "email",
            "",
            task_id,
            "",
            "immediate",
            2,
            None,
            "[]",
            status,
            0,
            None,
            None,
            None,
            None,
            1,
            7.0,
            0.8,
            "fresh",
            seizure_state,
            "internal",
            metadata_json,
            now_iso,
            now_iso,
            "immediate",
            None,
            "{}",
            None,
        ),
    )
    conn.commit()


def test_finalize_assignments_completed_heartbeat_requires_review():
    conn = _conn()
    _insert_task(
        conn,
        task_id="email:1",
        status=task_hub.TASK_STATUS_IN_PROGRESS,
        metadata_json='{"dispatch":{"active_assignment_id":"asg-1","active_provider_session_id":"daemon_simone"}}',
        seizure_state="seized",
    )
    conn.execute(
        """
        INSERT INTO task_hub_assignments (
            assignment_id, task_id, agent_id, provider_session_id, state, started_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("asg-1", "email:1", "heartbeat:daemon_simone", "daemon_simone", "running", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    result = task_hub.finalize_assignments(
        conn,
        assignment_ids=["asg-1"],
        state="completed",
        result_summary="completed",
        reopen_in_progress=True,
        policy="heartbeat",
    )

    item = task_hub.get_item(conn, "email:1")
    assert result["reviewed"] == 1
    assert item["status"] == task_hub.TASK_STATUS_REVIEW
    assert item["metadata"]["dispatch"]["last_disposition_reason"] == "heartbeat_completed_without_disposition"
    assert item["metadata"]["dispatch"]["completion_unverified"] is True


def test_reconcile_task_lifecycle_repairs_orphaned_and_unverified_completed():
    conn = _conn()
    _insert_task(
        conn,
        task_id="email:open-me",
        status=task_hub.TASK_STATUS_IN_PROGRESS,
        metadata_json='{"dispatch":{"active_assignment_id":"","active_provider_session_id":""}}',
        seizure_state="unseized",
    )
    _insert_task(
        conn,
        task_id="email:review-me",
        status=task_hub.TASK_STATUS_COMPLETED,
        metadata_json='{"dispatch":{"last_disposition_reason":"heartbeat_auto_completed"}}',
    )

    result = task_hub.reconcile_task_lifecycle(conn, running_session_ids=set())

    reopened = task_hub.get_item(conn, "email:open-me")
    reviewed = task_hub.get_item(conn, "email:review-me")
    assert result["reopened"] == 1
    assert result["completion_flagged"] == 1
    assert reopened["status"] == task_hub.TASK_STATUS_OPEN
    assert reopened["metadata"]["dispatch"]["last_disposition_reason"] == "reconciled_orphaned_in_progress"
    assert reviewed["status"] == task_hub.TASK_STATUS_REVIEW
    assert reviewed["metadata"]["dispatch"]["completion_unverified"] is True


def test_release_stale_assignments_accepts_multiple_prefixes():
    conn = _conn()
    _insert_task(
        conn,
        task_id="email:stale",
        status=task_hub.TASK_STATUS_IN_PROGRESS,
        metadata_json='{"dispatch":{"active_assignment_id":"asg-stale"}}',
        seizure_state="seized",
    )
    conn.execute(
        """
        INSERT INTO task_hub_assignments (
            assignment_id, task_id, agent_id, provider_session_id, state, started_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("asg-stale", "email:stale", "todo:daemon_simone", "daemon_simone", "running", "2026-03-01T00:00:00+00:00"),
    )
    conn.commit()

    result = task_hub.release_stale_assignments(
        conn,
        agent_id_prefix=["heartbeat:", "todo:"],
        stale_after_seconds=60,
    )

    item = task_hub.get_item(conn, "email:stale")
    assert result["finalized"] == 1
    assert item["status"] == task_hub.TASK_STATUS_OPEN
