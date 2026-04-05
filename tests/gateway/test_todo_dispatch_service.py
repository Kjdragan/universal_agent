from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from universal_agent.gateway import GatewaySession
from universal_agent.services.todo_dispatch_service import (
    ToDoDispatchService,
    build_execution_manifest,
    build_todo_execution_prompt,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


@pytest.mark.asyncio
async def test_todo_dispatch_service_executes_claimed_tasks(monkeypatch):
    conn = _conn()
    callback = AsyncMock(return_value={"decision": "accepted", "turn_id": "turn-1"})
    service = ToDoDispatchService(execution_callback=callback)
    session = GatewaySession(
        session_id="daemon_simone_todo",
        user_id="daemon",
        workspace_dir="/tmp/daemon_simone_todo",
        metadata={"source": "daemon", "session_role": "todo_execution", "run_kind": "todo_execution"},
    )

    monkeypatch.setattr("universal_agent.durable.db.connect_runtime_db", lambda *a, **k: conn)
    monkeypatch.setattr(
        "universal_agent.services.execution_run_service.allocate_execution_run",
        lambda **kwargs: SimpleNamespace(run_id="run_todo_001", workspace_dir="/tmp/run_todo_001"),
    )
    monkeypatch.setattr(
        "universal_agent.services.dispatch_service.dispatch_sweep",
        lambda _conn, **kwargs: [
            {
                "task_id": "email:1",
                "assignment_id": "asg-1",
                "title": "Weather report",
                "description": "Get the Houston weather",
                "metadata": {
                    "delivery_mode": "standard_report",
                    "workflow_manifest": build_execution_manifest(
                        user_input="Get the Houston weather",
                        delivery_mode="standard_report",
                        final_channel="email",
                    ),
                },
                "_routing": {"agent_id": "simone"},
            }
        ],
    )
    monkeypatch.setattr(
        "universal_agent.services.capacity_governor.CapacityGovernor.get_instance",
        lambda: type("Governor", (), {"can_dispatch": staticmethod(lambda: (True, "capacity_available"))})(),
    )
    monkeypatch.setattr(
        "universal_agent.services.capacity_governor.capacity_snapshot",
        lambda: {"available_slots": 1, "active_slots": 0, "max_concurrent": 2, "in_backoff": False},
    )
    monkeypatch.setattr(
        "universal_agent.task_hub.get_agent_activity",
        lambda _conn: {"active_assignments": [{"agent_id": "todo:daemon_simone_todo", "task_id": "email:x", "title": "Existing"}]},
    )

    service.register_session(session)
    await service._process_session(session)

    callback.assert_awaited_once()
    called_session_id, request = callback.await_args.args
    assert called_session_id == "daemon_simone_todo"
    assert request.metadata["source"] == "todo_dispatcher"
    assert request.metadata["run_kind"] == "todo_execution"
    assert request.metadata["claimed_task_ids"] == ["email:1"]
    assert "already claimed" in request.user_input.lower()
    assert "do not re-triage" in request.user_input.lower()
    assert "taskstop" in request.user_input.lower()
    assert "capacity snapshot" in request.user_input.lower()
    assert "delivery contract" in request.user_input.lower()
    assert "work item 1" in request.user_input.lower()
    assert "workflow_kind=research_report_email" in request.user_input
    assert "vp.general.primary" not in request.user_input
    assert "systemctl --user start ua-vp-general.service" not in request.user_input


@pytest.mark.asyncio
async def test_todo_dispatch_service_requeues_when_capacity_blocked(monkeypatch):
    conn = _conn()
    callback = AsyncMock()
    service = ToDoDispatchService(execution_callback=callback)
    session = GatewaySession(
        session_id="daemon_simone_todo",
        user_id="daemon",
        workspace_dir="/tmp/daemon_simone_todo",
        metadata={"source": "daemon", "session_role": "todo_execution", "run_kind": "todo_execution"},
    )

    monkeypatch.setattr("universal_agent.durable.db.connect_runtime_db", lambda *a, **k: conn)
    monkeypatch.setattr(
        "universal_agent.services.capacity_governor.CapacityGovernor.get_instance",
        lambda: type("Governor", (), {"can_dispatch": staticmethod(lambda: (False, "capacity_full"))})(),
    )

    await service._process_session(session)

    callback.assert_not_called()
    assert "daemon_simone_todo" in service.wake_sessions


def test_todo_dispatch_service_ignores_non_todo_sessions():
    service = ToDoDispatchService()
    session = GatewaySession(
        session_id="session_hook_agentmail_test",
        user_id="webhook",
        workspace_dir="/tmp/hook",
        metadata={"source": "webhook", "session_role": "email_triage", "run_kind": "email_triage"},
    )

    service.register_session(session)

    assert service.active_sessions == {}


def test_todo_dispatch_service_emits_utc_timestamps():
    events: list[dict[str, object]] = []
    service = ToDoDispatchService(event_callback=events.append)
    session = GatewaySession(
        session_id="daemon_simone_todo",
        user_id="daemon",
        workspace_dir="/tmp/daemon_simone_todo",
        metadata={"source": "daemon", "session_role": "todo_execution", "run_kind": "todo_execution"},
    )

    service.register_session(session)
    service.request_dispatch_now("daemon_simone_todo")
    service.unregister_session("daemon_simone_todo")

    assert len(events) == 3
    for event in events:
        timestamp = str(event.get("timestamp") or "")
        assert timestamp.endswith("+00:00")


def test_build_execution_manifest_supports_interactive_email():
    manifest = build_execution_manifest(
        user_input="Write a short poem about a rabbit and email it to me.",
        delivery_mode="interactive_email",
        final_channel="email",
    )

    assert manifest["workflow_kind"] == "interactive_answer_email"
    assert manifest["delivery_mode"] == "interactive_email"
    assert manifest["requires_pdf"] is False
    assert manifest["final_channel"] == "email"


def test_build_todo_execution_prompt_uses_mode_specific_delivery_contract():
    prompt = build_todo_execution_prompt(
        claimed_items=[
            {
                "task_id": "chat:1",
                "title": "Write a poem",
                "description": "Write a poem and email it.",
                "metadata": {
                    "delivery_mode": "interactive_email",
                    "workflow_manifest": build_execution_manifest(
                        user_input="Write a poem and email it.",
                        delivery_mode="interactive_email",
                        final_channel="email",
                    ),
                },
            }
        ],
        capacity_snapshot_data={"available_slots": 1, "active_slots": 0, "max_concurrent": 2, "in_backoff": False},
        active_assignments=[],
        origin_label="interactive_chat:session_test",
    )

    assert "interactive_email" in prompt
    assert "direct final email aligned to the user's request" in prompt
    assert "executive summary in the body" not in prompt
