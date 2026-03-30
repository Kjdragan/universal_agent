from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock

import pytest

from universal_agent.gateway import GatewaySession
from universal_agent.services.todo_dispatch_service import ToDoDispatchService


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
        session_id="daemon_simone",
        user_id="daemon",
        workspace_dir="/tmp/daemon_simone",
        metadata={"source": "daemon"},
    )

    monkeypatch.setattr("universal_agent.durable.db.connect_runtime_db", lambda *a, **k: conn)
    monkeypatch.setattr(
        "universal_agent.services.dispatch_service.dispatch_sweep",
        lambda _conn, **kwargs: [
            {
                "task_id": "email:1",
                "assignment_id": "asg-1",
                "title": "Weather report",
                "description": "Get the Houston weather",
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
        lambda _conn: {"active_assignments": [{"agent_id": "todo:daemon_simone", "task_id": "email:x", "title": "Existing"}]},
    )

    await service._process_session(session)

    callback.assert_awaited_once()
    called_session_id, request = callback.await_args.args
    assert called_session_id == "daemon_simone"
    assert request.metadata["source"] == "todo_dispatcher"
    assert request.metadata["claimed_task_ids"] == ["email:1"]
    assert "batch triage" in request.user_input.lower()
    assert "capacity snapshot" in request.user_input.lower()


@pytest.mark.asyncio
async def test_todo_dispatch_service_requeues_when_capacity_blocked(monkeypatch):
    conn = _conn()
    callback = AsyncMock()
    service = ToDoDispatchService(execution_callback=callback)
    session = GatewaySession(
        session_id="daemon_simone",
        user_id="daemon",
        workspace_dir="/tmp/daemon_simone",
        metadata={"source": "daemon"},
    )

    monkeypatch.setattr("universal_agent.durable.db.connect_runtime_db", lambda *a, **k: conn)
    monkeypatch.setattr(
        "universal_agent.services.capacity_governor.CapacityGovernor.get_instance",
        lambda: type("Governor", (), {"can_dispatch": staticmethod(lambda: (False, "capacity_full"))})(),
    )

    await service._process_session(session)

    callback.assert_not_called()
    assert "daemon_simone" in service.wake_sessions
