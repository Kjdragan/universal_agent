from __future__ import annotations

import copy
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from universal_agent import gateway_server
from universal_agent import task_hub
from universal_agent.gateway import GatewaySession
from universal_agent.services.todo_dispatch_service import ToDoDispatchService


def _db_connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_email_task(conn: sqlite3.Connection, *, task_id: str, title: str, score: float = 7.0) -> None:
    task_hub.ensure_schema(conn)
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "title": title,
            "description": f"Execute {title}",
            "project_key": "immediate",
            "priority": 2,
            "source_kind": "email",
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
            "score": score,
            "score_confidence": 0.9,
            "trigger_type": "immediate",
            "labels": ["agent-ready", "email"],
            "metadata": {"delivery_mode": "standard_report", "canonical_execution_owner": "todo_dispatcher"},
        },
    )


def _ensure_email_mapping_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS email_task_mappings (
            thread_id TEXT,
            task_id TEXT,
            subject TEXT,
            sender_email TEXT,
            status TEXT,
            last_message_id TEXT,
            message_count INTEGER,
            workflow_run_id TEXT,
            workflow_attempt_id TEXT,
            provider_session_id TEXT,
            email_sent_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()


def _insert_email_mapping(conn: sqlite3.Connection, *, task_id: str, provider_session_id: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO email_task_mappings (
            thread_id, task_id, subject, sender_email, status, last_message_id, message_count,
            workflow_run_id, workflow_attempt_id, provider_session_id, email_sent_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"thread-{task_id}",
            task_id,
            f"Subject for {task_id}",
            "kevin@example.com",
            "active",
            f"msg-{task_id}",
            1,
            "run-1",
            "attempt-1",
            provider_session_id,
            "",
            now_iso,
            now_iso,
        ),
    )
    conn.commit()


def _wire_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "activity_state.db"
    runtime_db_path = tmp_path / "runtime_state.db"

    def _connect_runtime_db(db_path_arg: str | None = None) -> sqlite3.Connection:
        resolved = Path(db_path_arg) if db_path_arg else runtime_db_path
        return _db_connect(resolved)

    monkeypatch.setattr("universal_agent.durable.db.connect_runtime_db", _connect_runtime_db)
    monkeypatch.setattr("universal_agent.durable.db.get_activity_db_path", lambda: str(db_path))
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(db_path))
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(
        "universal_agent.services.capacity_governor.CapacityGovernor.get_instance",
        lambda: type("Governor", (), {"can_dispatch": staticmethod(lambda: (True, "capacity_available"))})(),
    )
    monkeypatch.setattr(
        "universal_agent.services.capacity_governor.capacity_snapshot",
        lambda: {
            "available_slots": 1,
            "active_slots": 0,
            "max_concurrent": 2,
            "in_backoff": False,
        },
    )
    monkeypatch.setattr(gateway_server, "list_approvals", lambda status="pending": [])
    monkeypatch.setattr(gateway_server, "_heartbeat_service", None)
    monkeypatch.setenv("UA_HEARTBEAT_INTERVAL", "15m")
    monkeypatch.setattr(
        gateway_server,
        "_todo_dispatch_runtime_state",
        copy.deepcopy(gateway_server._todo_dispatch_runtime_state),
    )
    return db_path


def _make_session(tmp_path: Path, *, session_id: str = "daemon_simone_todo") -> tuple[GatewaySession, Path]:
    workspace = tmp_path / "run_daemon_simone_todo_20260330_120000_abcd1234"
    workspace.mkdir()
    (workspace / "run.log").write_text("run log", encoding="utf-8")
    (workspace / "transcript.md").write_text("transcript", encoding="utf-8")
    session = GatewaySession(
        session_id=session_id,
        user_id="daemon",
        workspace_dir=str(workspace),
        metadata={"source": "daemon", "session_role": "todo_execution", "run_kind": "todo_execution"},
    )
    return session, workspace


@pytest.mark.asyncio
async def test_todo_pipeline_completed_without_explicit_disposition_flows_to_review(monkeypatch, tmp_path):
    db_path = _wire_runtime(monkeypatch, tmp_path)
    session, _workspace = _make_session(tmp_path)
    callback = AsyncMock(return_value={"decision": "accepted", "turn_id": "turn-1"})
    service = ToDoDispatchService(
        execution_callback=callback,
        event_callback=gateway_server._todo_dispatch_runtime_record,
    )
    monkeypatch.setattr(gateway_server, "_todo_dispatch_service", service)
    service.register_session(session)

    with _db_connect(db_path) as conn:
        _seed_email_task(conn, task_id="email:review-flow", title="Houston weather")
        _ensure_email_mapping_schema(conn)
        _insert_email_mapping(conn, task_id="email:review-flow", provider_session_id=session.session_id)

    await service._process_session(session)

    callback.assert_awaited_once()
    with _db_connect(db_path) as conn:
        item = task_hub.get_item(conn, "email:review-flow")
        history = task_hub.get_task_history(conn, task_id="email:review-flow", limit=10)
        assert item is not None
        assert item["status"] == task_hub.TASK_STATUS_IN_PROGRESS
        assert item["seizure_state"] == "seized"
        assert history["assignments"][0]["agent_id"] == "todo:daemon_simone_todo"
        assignment_id = history["assignments"][0]["assignment_id"]
    with _db_connect(tmp_path / "runtime_state.db") as runtime_conn:
        runtime_tables = runtime_conn.execute(
            "SELECT COUNT(*) AS c FROM sqlite_master WHERE type='table' AND name='task_hub_items'"
        ).fetchone()
        assert int(runtime_tables["c"]) == 0

    with _db_connect(db_path) as conn:
        result = task_hub.finalize_assignments(
            conn,
            assignment_ids=[assignment_id],
            state="completed",
            result_summary="run finished without explicit disposition",
            reopen_in_progress=True,
            policy="todo",
        )
        item = task_hub.get_item(conn, "email:review-flow")
        assert result["reviewed"] == 1
        assert item is not None
        assert item["status"] == task_hub.TASK_STATUS_REVIEW
        assert item["metadata"]["dispatch"]["completion_unverified"] is True

    overview = await gateway_server.dashboard_todolist_overview()
    queue = await gateway_server.dashboard_todolist_agent_queue(offset=0, limit=20, status="pending")
    history_response = await gateway_server.dashboard_todolist_task_history("email:review-flow", limit=20)

    queue_item = next(item for item in queue["items"] if item["task_id"] == "email:review-flow")
    assert overview["todo_dispatch"]["registered_session_count"] == 1
    assert overview["todo_dispatch"]["last_claimed_session_id"] == "daemon_simone_todo"
    assert overview["todo_dispatch"]["last_dispatch_decision"] == "accepted"
    assert queue_item["board_lane"] == "needs_review"
    assert queue_item["requires_simone_review"] is True
    assert queue_item["delivery_mode"] == "standard_report"
    assert queue_item["session_role"] == "todo_execution"
    assert queue_item["run_kind"] == "todo_execution"
    assert history_response["reconciliation"]["completion_unverified"] is True
    assert history_response["email_mapping"]["thread_id"] == "thread-email:review-flow"
    assert history_response["assignments"][0]["links"]["run_log_href"]
    assert history_response["assignments"][0]["session_role"] == "todo_execution"
    assert history_response["canonical_execution"]["session_id"] == session.session_id
    assert history_response["artifacts"]["transcript_href"]


@pytest.mark.asyncio
async def test_todo_pipeline_explicit_completion_stays_completed_and_visible(monkeypatch, tmp_path):
    db_path = _wire_runtime(monkeypatch, tmp_path)
    session, _workspace = _make_session(tmp_path)
    callback = AsyncMock(return_value={"decision": "accepted", "turn_id": "turn-2"})
    service = ToDoDispatchService(
        execution_callback=callback,
        event_callback=gateway_server._todo_dispatch_runtime_record,
    )
    monkeypatch.setattr(gateway_server, "_todo_dispatch_service", service)
    service.register_session(session)

    with _db_connect(db_path) as conn:
        _seed_email_task(conn, task_id="email:completed-flow", title="Houston poem")
        _ensure_email_mapping_schema(conn)
        _insert_email_mapping(conn, task_id="email:completed-flow", provider_session_id=session.session_id)

    await service._process_session(session)

    with _db_connect(db_path) as conn:
        task_hub.perform_task_action(
            conn,
            task_id="email:completed-flow",
            action="complete",
            reason="delivered to Kevin",
            agent_id="todo:daemon_simone_todo",
        )
        history = task_hub.get_task_history(conn, task_id="email:completed-flow", limit=10)
        assignment_id = history["assignments"][0]["assignment_id"]
        result = task_hub.finalize_assignments(
            conn,
            assignment_ids=[assignment_id],
            state="completed",
            result_summary="run finished after explicit disposition",
            reopen_in_progress=True,
            policy="todo",
        )
        item = task_hub.get_item(conn, "email:completed-flow")
        assert result["completed"] == 0
        assert result["finalized"] == 0
        assert item is not None
        assert item["status"] == task_hub.TASK_STATUS_COMPLETED

    completed = await gateway_server.dashboard_todolist_agent_queue(offset=0, limit=20, status="completed")
    history_response = await gateway_server.dashboard_todolist_task_history("email:completed-flow", limit=20)

    completed_item = next(item for item in completed["items"] if item["task_id"] == "email:completed-flow")
    assert completed_item["board_lane"] == "completed"
    assert completed_item["requires_simone_review"] is False
    assert history_response["reconciliation"]["completion_unverified"] is False
    assert history_response["assignments"][0]["state"] == "completed"


@pytest.mark.asyncio
async def test_todolist_overview_flags_sleeping_dispatch_targets(monkeypatch):
    monkeypatch.setattr(gateway_server, "_heartbeat_service", None)
    monkeypatch.setattr(gateway_server, "_todo_dispatch_service", None)
    monkeypatch.setattr(gateway_server, "list_approvals", lambda status="pending": [])
    monkeypatch.setenv("UA_HEARTBEAT_INTERVAL", "15m")
    monkeypatch.setattr(
        gateway_server,
        "_todo_dispatch_runtime_state",
        copy.deepcopy(gateway_server._todo_dispatch_runtime_state),
    )

    gateway_server._todo_dispatch_runtime_record(
        {
            "type": "todo_dispatch_wake_requested",
            "session_id": "vp.general.primary",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "registered": False,
        }
    )

    response = await gateway_server.dashboard_todolist_overview()

    assert response["status"] == "ok"
    assert response["todo_dispatch"]["last_wake_requested_session_id"] == "vp.general.primary"
    assert response["todo_dispatch"]["last_wake_registered"] is False
    assert response["todo_dispatch"]["sleeping_session_warning"] is True
