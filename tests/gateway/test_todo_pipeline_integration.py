from __future__ import annotations

import copy
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from universal_agent import gateway_server
from universal_agent import task_hub
from universal_agent.gateway import GatewayRequest, GatewaySession
from universal_agent.mission_guardrails import MissionGuardrailTracker, build_mission_contract
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


def _seed_tracked_chat_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    title: str,
    source_ref: str,
    description: str = "Create the requested report and email it to the user.",
    delivery_mode: str = "standard_report",
    final_channel: str = "email",
) -> None:
    task_hub.ensure_schema(conn)
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "title": title,
            "description": description,
            "project_key": "immediate",
            "priority": 1,
            "source_kind": "chat_panel",
            "source_ref": source_ref,
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
            "score": 8.0,
            "score_confidence": 0.95,
            "trigger_type": "immediate",
            "labels": ["chat-panel", "interactive"],
            "metadata": {
                "delivery_mode": delivery_mode,
                "canonical_execution_owner": "interactive_chat_session",
                "intake_channel": "chat_panel",
                "workflow_manifest": {
                    "workflow_kind": "interactive_answer" if final_channel == "chat" else "research_report_email",
                    "delivery_mode": delivery_mode,
                    "requires_pdf": False,
                    "final_channel": final_channel,
                    "canonical_executor": "simone_first",
                },
            },
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
        # Record outbound delivery proof before completing — the email
        # verification gate requires this to allow completion.
        task_hub.record_task_outbound_delivery(
            conn,
            task_id="email:completed-flow",
            channel="agentmail",
            message_id="msg-completed-flow-001",
        )
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


def test_todo_execution_lifecycle_auto_links_vp_dispatch(monkeypatch, tmp_path):
    db_path = _wire_runtime(monkeypatch, tmp_path)
    session, _workspace = _make_session(tmp_path)

    with _db_connect(db_path) as conn:
        _seed_email_task(conn, task_id="email:auto-delegate", title="Mythos report")
        history = task_hub.claim_next_dispatch_tasks(
            conn,
            limit=1,
            agent_id="todo:daemon_simone_todo",
            provider_session_id=session.session_id,
            workspace_dir=session.workspace_dir,
        )
        assignment_id = history[0]["assignment_id"]

    tracker = MissionGuardrailTracker(
        build_mission_contract("Create a comprehensive report and email it to me."),
        run_kind="todo_execution",
    )
    tracker.record_tool_call(
        "mcp__internal__vp_dispatch_mission",
        tool_input={
            "vp_id": "vp.general.primary",
            "objective": "Handle task email:auto-delegate and produce the full report",
        },
    )
    tracker.record_tool_result(
        "mcp__internal__vp_dispatch_mission",
        tool_input={
            "vp_id": "vp.general.primary",
            "objective": "Handle task email:auto-delegate and produce the full report",
        },
        tool_result='{"ok": true, "mission_id": "mission-auto", "vp_id": "vp.general.primary"}',
    )

    result = gateway_server._enforce_todo_execution_lifecycle(
        session=session,
        request=GatewayRequest(
            user_input="Create a comprehensive report and email it to me.",
            metadata={
                "source": "todo_dispatcher",
                "run_kind": "todo_execution",
                "claimed_task_ids": ["email:auto-delegate"],
                "claimed_assignment_ids": [assignment_id],
            },
        ),
        mission_tracker=tracker,
        goal_satisfaction=tracker.evaluate(),
    )

    with _db_connect(db_path) as conn:
        item = task_hub.get_item(conn, "email:auto-delegate")
        assignment = conn.execute(
            "SELECT state, ended_at FROM task_hub_assignments WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchone()

    assert result["passed"] is True
    assert result["stage_status"] == "delegated"
    assert result["observed"]["auto_delegate_applied"] is True
    assert item is not None
    assert item["status"] == task_hub.TASK_STATUS_DELEGATED
    assert item["metadata"]["delegation"]["mission_id"] == "mission-auto"
    assert assignment["state"] == "completed"
    assert assignment["ended_at"]


def test_todo_execution_lifecycle_reopens_task_when_no_mutation(monkeypatch, tmp_path):
    db_path = _wire_runtime(monkeypatch, tmp_path)
    session, _workspace = _make_session(tmp_path)

    with _db_connect(db_path) as conn:
        _seed_email_task(conn, task_id="email:no-mutation", title="Smoke task")
        history = task_hub.claim_next_dispatch_tasks(
            conn,
            limit=1,
            agent_id="todo:daemon_simone_todo",
            provider_session_id=session.session_id,
            workspace_dir=session.workspace_dir,
        )
        assignment_id = history[0]["assignment_id"]

    tracker = MissionGuardrailTracker(
        build_mission_contract("Create a comprehensive report and email it to me."),
        run_kind="todo_execution",
    )

    result = gateway_server._enforce_todo_execution_lifecycle(
        session=session,
        request=GatewayRequest(
            user_input="Create a comprehensive report and email it to me.",
            metadata={
                "source": "todo_dispatcher",
                "run_kind": "todo_execution",
                "claimed_task_ids": ["email:no-mutation"],
                "claimed_assignment_ids": [assignment_id],
            },
        ),
        mission_tracker=tracker,
        goal_satisfaction=tracker.evaluate(),
    )

    with _db_connect(db_path) as conn:
        item = task_hub.get_item(conn, "email:no-mutation")
        assignment = conn.execute(
            "SELECT state, ended_at, result_summary FROM task_hub_assignments WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchone()

    assert result["passed"] is False
    assert result["missing"][0]["requirement"] == "lifecycle_mutation"
    assert item is not None
    assert item["status"] == task_hub.TASK_STATUS_OPEN
    assert item["seizure_state"] == "unseized"
    assert assignment["state"] == "failed"
    assert assignment["ended_at"]


def test_todo_execution_auto_completes_chat_task_after_final_delivery(monkeypatch, tmp_path):
    db_path = _wire_runtime(monkeypatch, tmp_path)
    session, _workspace = _make_session(tmp_path)

    with _db_connect(db_path) as conn:
        _seed_tracked_chat_task(
            conn,
            task_id="chat:session_chat_repeat:turn_001",
            title="AI model releases report",
            source_ref=session.session_id,
        )
        history = task_hub.claim_next_dispatch_tasks(
            conn,
            limit=1,
            agent_id=f"todo:{session.session_id}",
            provider_session_id=session.session_id,
            workspace_dir=session.workspace_dir,
        )
        assignment_id = history[0]["assignment_id"]
        task_hub.record_task_outbound_delivery(
            conn,
            task_id="chat:session_chat_repeat:turn_001",
            channel="agentmail",
            message_id="msg-chat-1",
            sent_at="2026-03-31T15:00:00Z",
        )

    tracker = MissionGuardrailTracker(
        build_mission_contract("Create a comprehensive report and email it to me."),
        run_kind="todo_execution",
    )
    tracker.record_tool_call(
        "mcp__agentmail__send_message",
        tool_input={"to": "kevin@example.com", "subject": "AI model releases report"},
    )

    result = gateway_server._enforce_todo_execution_lifecycle(
        session=session,
        request=GatewayRequest(
            user_input="Create a comprehensive report and email it to me.",
            metadata={
                "source": "todo_dispatcher",
                "run_kind": "todo_execution",
                "claimed_task_ids": ["chat:session_chat_repeat:turn_001"],
                "claimed_assignment_ids": [assignment_id],
            },
        ),
        mission_tracker=tracker,
        goal_satisfaction=tracker.evaluate(),
    )

    with _db_connect(db_path) as conn:
        item = task_hub.get_item(conn, "chat:session_chat_repeat:turn_001")
        assignment = conn.execute(
            "SELECT state, ended_at, result_summary FROM task_hub_assignments WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchone()

    assert result["passed"] is True
    assert result["stage_status"] == "completed"
    assert result["terminal"] is True
    assert result["observed"]["auto_completed_after_delivery"] is True
    assert result["observed"]["auto_completed_task_ids"] == ["chat:session_chat_repeat:turn_001"]
    assert item is not None
    assert item["status"] == task_hub.TASK_STATUS_COMPLETED
    assert item["seizure_state"] == "completed"
    assert item["completion_token"]
    assert assignment["state"] == "completed"
    assert assignment["ended_at"]


def test_todo_execution_auto_completes_interactive_chat_task_after_final_chat_delivery(monkeypatch, tmp_path):
    db_path = _wire_runtime(monkeypatch, tmp_path)
    session, _workspace = _make_session(tmp_path, session_id="session_chat_auto_complete")

    with _db_connect(db_path) as conn:
        _seed_tracked_chat_task(
            conn,
            task_id="chat:session_chat_auto_complete:turn_001",
            title="Summarize the latest session activity",
            source_ref=session.session_id,
            description="Summarize the latest session activity directly in chat.",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        history = task_hub.claim_next_dispatch_tasks(
            conn,
            limit=1,
            agent_id=f"todo:{session.session_id}",
            provider_session_id=session.session_id,
            workspace_dir=session.workspace_dir,
        )
        assignment_id = history[0]["assignment_id"]
        task_hub.record_task_outbound_delivery(
            conn,
            task_id="chat:session_chat_auto_complete:turn_001",
            channel="chat",
            message_id="turn_001",
            sent_at="2026-04-04T13:00:00Z",
        )

    tracker = MissionGuardrailTracker(
        build_mission_contract("Summarize the latest session activity directly in chat."),
        run_kind="todo_execution",
    )

    result = gateway_server._enforce_todo_execution_lifecycle(
        session=session,
        request=GatewayRequest(
            user_input="Summarize the latest session activity directly in chat.",
            metadata={
                "source": "chat_panel",
                "run_kind": "todo_execution",
                "claimed_task_ids": ["chat:session_chat_auto_complete:turn_001"],
                "claimed_assignment_ids": [assignment_id],
                "todo_final_response_delivered": True,
            },
        ),
        mission_tracker=tracker,
        goal_satisfaction=tracker.evaluate(),
    )

    with _db_connect(db_path) as conn:
        item = task_hub.get_item(conn, "chat:session_chat_auto_complete:turn_001")
        assignment = conn.execute(
            "SELECT state, ended_at, result_summary FROM task_hub_assignments WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchone()

    assert result["passed"] is True
    assert result["stage_status"] == "completed"
    assert result["terminal"] is True
    assert result["observed"]["auto_completed_after_delivery"] is True
    assert result["observed"]["auto_completed_task_ids"] == ["chat:session_chat_auto_complete:turn_001"]
    assert item is not None
    assert item["status"] == task_hub.TASK_STATUS_COMPLETED
    assert item["seizure_state"] == "completed"
    assert item["completion_token"]
    assert item["metadata"]["dispatch"]["outbound_delivery"]["channel"] == "chat"
    assert assignment["state"] == "completed"
    assert assignment["ended_at"]


def test_todo_execution_does_not_auto_complete_chat_task_without_durable_delivery_record(monkeypatch, tmp_path):
    db_path = _wire_runtime(monkeypatch, tmp_path)
    session, _workspace = _make_session(tmp_path, session_id="session_chat_missing_delivery")

    with _db_connect(db_path) as conn:
        _seed_tracked_chat_task(
            conn,
            task_id="chat:session_chat_missing_delivery:turn_001",
            title="Summarize the latest session activity",
            source_ref=session.session_id,
            description="Summarize the latest session activity directly in chat.",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        history = task_hub.claim_next_dispatch_tasks(
            conn,
            limit=1,
            agent_id=f"todo:{session.session_id}",
            provider_session_id=session.session_id,
            workspace_dir=session.workspace_dir,
        )
        assignment_id = history[0]["assignment_id"]

    tracker = MissionGuardrailTracker(
        build_mission_contract("Summarize the latest session activity directly in chat."),
        run_kind="todo_execution",
    )

    result = gateway_server._enforce_todo_execution_lifecycle(
        session=session,
        request=GatewayRequest(
            user_input="Summarize the latest session activity directly in chat.",
            metadata={
                "source": "chat_panel",
                "run_kind": "todo_execution",
                "claimed_task_ids": ["chat:session_chat_missing_delivery:turn_001"],
                "claimed_assignment_ids": [assignment_id],
                "todo_final_response_delivered": True,
            },
        ),
        mission_tracker=tracker,
        goal_satisfaction=tracker.evaluate(),
    )

    with _db_connect(db_path) as conn:
        item = task_hub.get_item(conn, "chat:session_chat_missing_delivery:turn_001")
        assignment = conn.execute(
            "SELECT state, ended_at, result_summary FROM task_hub_assignments WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchone()

    assert result["passed"] is False
    assert result["missing"][0]["requirement"] == "lifecycle_mutation"
    assert item is not None
    assert item["status"] == task_hub.TASK_STATUS_OPEN
    assert item["seizure_state"] == "unseized"
    assert assignment["state"] == "failed"
    assert assignment["ended_at"]


def test_todo_execution_only_auto_completes_tasks_with_verified_delivery(monkeypatch, tmp_path):
    db_path = _wire_runtime(monkeypatch, tmp_path)
    session, _workspace = _make_session(tmp_path, session_id="session_chat_partial_delivery")

    with _db_connect(db_path) as conn:
        _seed_tracked_chat_task(
            conn,
            task_id="chat:session_chat_partial_delivery:turn_001",
            title="Delivered chat answer",
            source_ref=session.session_id,
            description="Summarize the latest session activity directly in chat.",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        _seed_tracked_chat_task(
            conn,
            task_id="chat:session_chat_partial_delivery:turn_002",
            title="Undelivered chat answer",
            source_ref=session.session_id,
            description="Summarize the latest session activity directly in chat.",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        history = task_hub.claim_next_dispatch_tasks(
            conn,
            limit=2,
            agent_id=f"todo:{session.session_id}",
            provider_session_id=session.session_id,
            workspace_dir=session.workspace_dir,
        )
        assignment_ids = [row["assignment_id"] for row in history]
        task_hub.record_task_outbound_delivery(
            conn,
            task_id="chat:session_chat_partial_delivery:turn_001",
            channel="chat",
            message_id="turn_001",
            sent_at="2026-04-04T13:00:00Z",
        )

    tracker = MissionGuardrailTracker(
        build_mission_contract("Summarize the latest session activity directly in chat."),
        run_kind="todo_execution",
    )

    result = gateway_server._enforce_todo_execution_lifecycle(
        session=session,
        request=GatewayRequest(
            user_input="Summarize the latest session activity directly in chat.",
            metadata={
                "source": "chat_panel",
                "run_kind": "todo_execution",
                "claimed_task_ids": [
                    "chat:session_chat_partial_delivery:turn_001",
                    "chat:session_chat_partial_delivery:turn_002",
                ],
                "claimed_assignment_ids": assignment_ids,
                "todo_final_response_delivered": True,
            },
        ),
        mission_tracker=tracker,
        goal_satisfaction=tracker.evaluate(),
    )

    with _db_connect(db_path) as conn:
        delivered = task_hub.get_item(conn, "chat:session_chat_partial_delivery:turn_001")
        undelivered = task_hub.get_item(conn, "chat:session_chat_partial_delivery:turn_002")

    assert result["passed"] is False
    assert result["missing"][0]["requirement"] == "lifecycle_mutation"
    assert delivered is not None
    assert delivered["status"] == task_hub.TASK_STATUS_COMPLETED
    assert undelivered is not None
    assert undelivered["status"] == task_hub.TASK_STATUS_OPEN


@pytest.mark.asyncio
async def test_todo_queue_shows_self_reviewed_delivery_as_completed(monkeypatch, tmp_path):
    db_path = _wire_runtime(monkeypatch, tmp_path)
    session, _workspace = _make_session(tmp_path, session_id="session_chat_self_review")

    with _db_connect(db_path) as conn:
        _seed_tracked_chat_task(
            conn,
            task_id="chat:session_chat_self_review:turn_001",
            title="Houston weather poem",
            source_ref=session.session_id,
            description="Get the forecast for Houston, write a poem, then email it.",
            delivery_mode="standard_report",
            final_channel="email",
        )
        history = task_hub.claim_next_dispatch_tasks(
            conn,
            limit=1,
            agent_id=f"todo:{session.session_id}",
            provider_session_id=session.session_id,
            workspace_dir=session.workspace_dir,
        )
        assignment_id = history[0]["assignment_id"]
        task_hub.record_task_outbound_delivery(
            conn,
            task_id="chat:session_chat_self_review:turn_001",
            channel="agentmail",
            message_id="msg-self-review",
            sent_at="2026-04-04T12:18:00Z",
        )
        result = task_hub.finalize_assignments(
            conn,
            assignment_ids=[assignment_id],
            state="failed",
            result_summary="todo_execution_missing_lifecycle_mutation",
            reopen_in_progress=True,
            policy="todo",
        )
        item = task_hub.get_item(conn, "chat:session_chat_self_review:turn_001")

    assert result["completed"] == 1
    assert item is not None
    assert item["status"] == task_hub.TASK_STATUS_COMPLETED
    assert item["metadata"]["dispatch"]["last_disposition_reason"] == "todo_self_reviewed_after_delivery"

    completed = await gateway_server.dashboard_todolist_agent_queue(offset=0, limit=20, status="completed")
    completed_item = next(item for item in completed["items"] if item["task_id"] == "chat:session_chat_self_review:turn_001")
    assert completed_item["board_lane"] == "completed"
    assert completed_item["requires_simone_review"] is False
