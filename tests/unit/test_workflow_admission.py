import sqlite3
from pathlib import Path

import universal_agent.workflow_admission as workflow_admission_module
from universal_agent.durable.db import connect_runtime_db
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import create_run_attempt, get_run, get_run_attempt, list_run_attempts, upsert_run
from universal_agent.workflow_admission import WorkflowAdmissionService, WorkflowTrigger


def _service(tmp_path: Path) -> WorkflowAdmissionService:
    return WorkflowAdmissionService(str((tmp_path / "runtime_state.db").resolve()))


def test_workflow_admission_creates_new_run_and_attempt(tmp_path: Path):
    service = _service(tmp_path)
    workspace_dir = tmp_path / "run_admission_1"
    decision = service.admit(
        WorkflowTrigger(
            run_kind="heartbeat_email_wake",
            trigger_source="agentmail",
            dedup_key="msg-1",
            payload_json='{"message_id":"msg-1"}',
            priority=1,
            run_policy="automation_ephemeral",
            interrupt_policy="defer_if_foreground",
        ),
        entrypoint="test_entrypoint",
        workspace_dir=str(workspace_dir),
    )

    assert decision.action == "start_new_run"
    assert decision.run_id
    assert decision.attempt_id
    assert (workspace_dir / "run_manifest.json").exists()
    assert (workspace_dir / "attempts" / "001" / "attempt_meta.json").exists()


def test_workflow_admission_skips_completed_duplicate(tmp_path: Path):
    service = _service(tmp_path)
    conn = connect_runtime_db(service.db_path)
    ensure_schema(conn)
    upsert_run(
        conn,
        run_id="run_done",
        entrypoint="test_entrypoint",
        run_spec={"workspace_dir": str((tmp_path / "run_done").resolve())},
        status="completed",
        workspace_dir=str((tmp_path / "run_done").resolve()),
        run_kind="heartbeat_email_wake",
        trigger_source="agentmail",
        dedup_key="msg-2",
    )
    conn.commit()
    conn.close()

    decision = service.admit(
        WorkflowTrigger(
            run_kind="heartbeat_email_wake",
            trigger_source="agentmail",
            dedup_key="msg-2",
            payload_json=None,
            priority=1,
            run_policy="automation_ephemeral",
            interrupt_policy="defer_if_foreground",
        ),
        entrypoint="test_entrypoint",
    )

    assert decision.action == "skip_duplicate"
    assert decision.run_id == "run_done"


def test_workflow_admission_defers_active_run(tmp_path: Path):
    service = _service(tmp_path)
    conn = connect_runtime_db(service.db_path)
    ensure_schema(conn)
    upsert_run(
        conn,
        run_id="run_active",
        entrypoint="test_entrypoint",
        run_spec={"workspace_dir": str((tmp_path / "run_active").resolve())},
        status="running",
        workspace_dir=str((tmp_path / "run_active").resolve()),
        run_kind="heartbeat_cron_wake",
        trigger_source="cron",
        dedup_key="cron:abc",
    )
    create_run_attempt(conn, "run_active", status="running")
    conn.commit()
    conn.close()

    decision = service.admit(
        WorkflowTrigger(
            run_kind="heartbeat_cron_wake",
            trigger_source="cron",
            dedup_key="cron:abc",
            payload_json=None,
            priority=1,
            run_policy="automation_ephemeral",
            interrupt_policy="defer_if_foreground",
        ),
        entrypoint="test_entrypoint",
    )

    assert decision.action == "defer"
    assert decision.run_id == "run_active"


def test_workflow_admission_retries_failed_run_with_new_attempt(tmp_path: Path):
    service = _service(tmp_path)
    conn = connect_runtime_db(service.db_path)
    ensure_schema(conn)
    upsert_run(
        conn,
        run_id="run_failed",
        entrypoint="test_entrypoint",
        run_spec={"workspace_dir": str((tmp_path / "run_failed").resolve())},
        status="failed",
        workspace_dir=str((tmp_path / "run_failed").resolve()),
        run_kind="csi_specialist_followup_dispatch",
        trigger_source="csi",
        dedup_key="csi:topic-1:event-1",
    )
    create_run_attempt(conn, "run_failed", status="failed")
    conn.commit()
    conn.close()

    decision = service.admit(
        WorkflowTrigger(
            run_kind="csi_specialist_followup_dispatch",
            trigger_source="csi",
            dedup_key="csi:topic-1:event-1",
            payload_json=None,
            priority=1,
            run_policy="automation_ephemeral",
            interrupt_policy="attach_if_same_dedup_key",
        ),
        entrypoint="test_entrypoint",
        retryable_failure=True,
        max_attempts=3,
    )

    assert decision.action == "start_new_attempt"
    assert decision.run_id == "run_failed"

    conn = connect_runtime_db(service.db_path)
    attempts = list_run_attempts(conn, "run_failed")
    conn.close()
    assert len(attempts) == 2


def test_workflow_admission_marks_completion_and_failure(tmp_path: Path):
    service = _service(tmp_path)
    decision = service.admit(
        WorkflowTrigger(
            run_kind="heartbeat_email_wake",
            trigger_source="agentmail",
            dedup_key="msg-3",
            payload_json=None,
            priority=1,
            run_policy="automation_ephemeral",
            interrupt_policy="defer_if_foreground",
        ),
        entrypoint="test_entrypoint",
    )
    assert decision.run_id and decision.attempt_id

    service.mark_completed(decision.run_id, attempt_id=decision.attempt_id, summary={"count": 2})

    conn = connect_runtime_db(service.db_path)
    row = get_run(conn, decision.run_id)
    conn.close()
    assert row is not None
    assert row["status"] == "completed"


def test_workflow_admission_mark_running_and_blocked(tmp_path: Path):
    service = _service(tmp_path)
    decision = service.admit(
        WorkflowTrigger(
            run_kind="youtube_tutorial_hook",
            trigger_source="webhook",
            dedup_key="video-1",
            payload_json='{"video_id":"video-1"}',
            priority=1,
            run_policy="automation_ephemeral",
            interrupt_policy="attach_if_same_dedup_key",
        ),
        entrypoint="test_entrypoint",
        workspace_dir=str((tmp_path / "run_youtube").resolve()),
    )
    assert decision.run_id and decision.attempt_id

    service.mark_running(
        decision.run_id,
        attempt_id=decision.attempt_id,
        provider_session_id="session_hook_yt_video_1",
        summary={"phase": "dispatch"},
    )
    service.mark_blocked(
        decision.run_id,
        attempt_id=decision.attempt_id,
        reason="pending_local_ingest",
        summary={"phase": "ingest"},
    )

    conn = connect_runtime_db(service.db_path)
    ensure_schema(conn)
    run_row = get_run(conn, decision.run_id)
    attempt_row = get_run_attempt(conn, decision.attempt_id)
    conn.close()
    assert run_row is not None
    assert attempt_row is not None
    assert run_row["status"] == "blocked"
    assert attempt_row["status"] == "blocked"
    assert attempt_row["provider_session_id"] == "session_hook_yt_video_1"


def test_workflow_admission_queue_retry_and_needs_review(tmp_path: Path):
    service = _service(tmp_path)
    workspace_dir = tmp_path / "run_retry"
    decision = service.admit(
        WorkflowTrigger(
            run_kind="cron_job_dispatch",
            trigger_source="cron",
            dedup_key="scheduled:job1:1",
            payload_json='{"job_id":"job1"}',
            priority=1,
            run_policy="automation_ephemeral",
            interrupt_policy="attach_if_same_dedup_key",
        ),
        entrypoint="test_entrypoint",
        workspace_dir=str(workspace_dir.resolve()),
    )
    assert decision.run_id and decision.attempt_id

    service.mark_running(decision.run_id, attempt_id=decision.attempt_id, provider_session_id="session_cron_1")
    retry_decision = service.queue_retry(
        WorkflowTrigger(
            run_kind="cron_job_dispatch",
            trigger_source="cron",
            dedup_key="scheduled:job1:1",
            payload_json='{"job_id":"job1"}',
            priority=1,
            run_policy="automation_ephemeral",
            interrupt_policy="attach_if_same_dedup_key",
        ),
        entrypoint="test_entrypoint",
        run_id=decision.run_id,
        attempt_id=decision.attempt_id,
        workspace_dir=str(workspace_dir.resolve()),
        failure_reason="execution timeout",
        failure_class="execution_timeout",
        max_attempts=3,
    )
    assert retry_decision.action == "start_new_attempt"

    conn = connect_runtime_db(service.db_path)
    ensure_schema(conn)
    attempts = list_run_attempts(conn, decision.run_id)
    conn.close()
    assert len(attempts) == 2
    assert attempts[-1]["status"] == "queued"
    assert (workspace_dir / "attempts" / "002" / "attempt_meta.json").exists()

    service.mark_needs_review(
        decision.run_id,
        attempt_id=retry_decision.attempt_id,
        reason="auth_required",
        failure_class="auth_required",
    )
    conn = connect_runtime_db(service.db_path)
    ensure_schema(conn)
    run_row = get_run(conn, decision.run_id)
    retry_row = get_run_attempt(conn, retry_decision.attempt_id)
    conn.close()
    assert run_row is not None
    assert retry_row is not None
    assert run_row["status"] == "needs_review"
    assert retry_row["status"] == "failed"


def test_workflow_admission_propagates_sqlite_lock_during_admit(tmp_path: Path, monkeypatch):
    """Verify that DB lock errors propagate so the async caller can retry.

    ``_run_with_retry`` was refactored to single-attempt execution.
    Lock-retry is now handled at the async caller level (hooks_service)
    using ``await asyncio.sleep()`` to avoid blocking the event loop.
    """
    import pytest as _pytest

    service = _service(tmp_path)
    original_upsert_run = workflow_admission_module.upsert_run

    def locked_upsert_run(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(workflow_admission_module, "upsert_run", locked_upsert_run)

    with _pytest.raises(sqlite3.OperationalError, match="database is locked"):
        service.admit(
            WorkflowTrigger(
                run_kind="youtube_tutorial_hook",
                trigger_source="webhook",
                dedup_key="video-lock-1",
                payload_json='{"video_id":"video-lock-1"}',
                priority=1,
                run_policy="automation_ephemeral",
                interrupt_policy="attach_if_same_dedup_key",
            ),
            entrypoint="test_entrypoint",
            workspace_dir=str((tmp_path / "run_lock_retry").resolve()),
        )
