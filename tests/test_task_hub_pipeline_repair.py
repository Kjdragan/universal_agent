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


def test_finalize_assignments_todo_with_final_draft_side_effects_moves_to_review():
    conn = _conn()
    conn.execute(
        "ALTER TABLE email_task_mappings ADD COLUMN final_draft_id TEXT NOT NULL DEFAULT ''"
    )
    conn.execute(
        "INSERT INTO email_task_mappings (task_id, email_sent_at, final_draft_id) VALUES (?, ?, ?)",
        ("email:draft-review", "", "draft-123"),
    )
    _insert_task(
        conn,
        task_id="email:draft-review",
        status=task_hub.TASK_STATUS_IN_PROGRESS,
        metadata_json='{"dispatch":{"active_assignment_id":"asg-draft","active_provider_session_id":"daemon_simone_todo"}}',
        seizure_state="seized",
    )
    conn.execute(
        """
        INSERT INTO task_hub_assignments (
            assignment_id, task_id, agent_id, provider_session_id, state, started_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("asg-draft", "email:draft-review", "todo:daemon_simone_todo", "daemon_simone_todo", "running", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    result = task_hub.finalize_assignments(
        conn,
        assignment_ids=["asg-draft"],
        state="failed",
        result_summary="todo_execution_missing_lifecycle_mutation",
        reopen_in_progress=True,
        policy="todo",
    )

    item = task_hub.get_item(conn, "email:draft-review")
    assert result["reviewed"] == 1
    assert result["reopened"] == 0
    assert item["status"] == task_hub.TASK_STATUS_REVIEW
    assert item["metadata"]["dispatch"]["last_disposition_reason"] == "todo_retryable_with_side_effects"
    assert item["metadata"]["dispatch"]["completion_unverified"] is True


def test_finalize_assignments_todo_with_generic_outbound_delivery_moves_to_review():
    conn = _conn()
    _insert_task(
        conn,
        task_id="chat:email-review",
        status=task_hub.TASK_STATUS_IN_PROGRESS,
        metadata_json='{"dispatch":{"active_assignment_id":"asg-chat","active_provider_session_id":"session_chat","outbound_delivery":{"channel":"agentmail","message_id":"msg-123","sent_at":"2026-03-31T15:00:00Z"}}}',
        seizure_state="seized",
    )
    conn.execute(
        """
        INSERT INTO task_hub_assignments (
            assignment_id, task_id, agent_id, provider_session_id, state, started_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("asg-chat", "chat:email-review", "todo:session_chat", "session_chat", "running", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    result = task_hub.finalize_assignments(
        conn,
        assignment_ids=["asg-chat"],
        state="failed",
        result_summary="todo_execution_missing_lifecycle_mutation",
        reopen_in_progress=True,
        policy="todo",
    )

    item = task_hub.get_item(conn, "chat:email-review")
    assert result["reviewed"] == 1
    assert result["reopened"] == 0
    assert item["status"] == task_hub.TASK_STATUS_REVIEW
    assert item["metadata"]["dispatch"]["last_disposition_reason"] == "todo_retryable_with_side_effects"
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


def test_delegate_action_closes_active_assignment_and_clears_dispatch():
    conn = _conn()
    _insert_task(
        conn,
        task_id="email:delegate-me",
        status=task_hub.TASK_STATUS_IN_PROGRESS,
        metadata_json='{"dispatch":{"active_assignment_id":"asg-delegate","active_provider_session_id":"daemon_simone_todo","active_workspace_dir":"/tmp/ws"}}',
        seizure_state="seized",
    )
    conn.execute(
        """
        INSERT INTO task_hub_assignments (
            assignment_id, task_id, agent_id, provider_session_id, state, started_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("asg-delegate", "email:delegate-me", "todo:daemon_simone_todo", "daemon_simone_todo", "running", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    updated = task_hub.perform_task_action(
        conn,
        task_id="email:delegate-me",
        action="delegate",
        reason="vp.general.primary",
        note="mission_id=mission-123",
        agent_id="todo:daemon_simone_todo",
    )

    assignment = conn.execute(
        "SELECT state, ended_at, result_summary FROM task_hub_assignments WHERE assignment_id = ?",
        ("asg-delegate",),
    ).fetchone()
    assert updated["status"] == task_hub.TASK_STATUS_DELEGATED
    assert updated["metadata"]["delegation"]["mission_id"] == "mission-123"
    assert updated["metadata"]["dispatch"].get("active_assignment_id") in {None, ""}
    assert assignment["state"] == "completed"
    assert assignment["ended_at"]


def test_reconcile_task_lifecycle_backfills_delegation_from_vp_mission():
    conn = _conn()
    _insert_task(
        conn,
        task_id="email:backfill",
        status=task_hub.TASK_STATUS_IN_PROGRESS,
        metadata_json='{"dispatch":{"active_assignment_id":"asg-backfill","active_provider_session_id":"daemon_simone_todo"}}',
        seizure_state="seized",
    )
    conn.execute(
        """
        INSERT INTO task_hub_assignments (
            assignment_id, task_id, agent_id, provider_session_id, state, started_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("asg-backfill", "email:backfill", "todo:daemon_simone_todo", "daemon_simone_todo", "running", "2026-03-30T20:27:48+00:00"),
    )
    conn.execute(
        """
        CREATE TABLE vp_missions (
            mission_id TEXT,
            vp_id TEXT,
            payload_json TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO vp_missions (mission_id, vp_id, payload_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            "mission-456",
            "vp.general.primary",
            '{"source_session_id":"daemon_simone_todo"}',
            "2026-03-30T20:27:49+00:00",
        ),
    )
    conn.commit()

    result = task_hub.reconcile_task_lifecycle(conn, running_session_ids=set())
    item = task_hub.get_item(conn, "email:backfill")
    assignment = conn.execute(
        "SELECT state, ended_at FROM task_hub_assignments WHERE assignment_id = ?",
        ("asg-backfill",),
    ).fetchone()

    assert result["delegated_backfilled"] == 1
    assert item["status"] == task_hub.TASK_STATUS_DELEGATED
    assert item["metadata"]["delegation"]["mission_id"] == "mission-456"
    assert assignment["state"] == "completed"
    assert assignment["ended_at"]
