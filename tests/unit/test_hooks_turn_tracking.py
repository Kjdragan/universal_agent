from __future__ import annotations

import asyncio
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from universal_agent.durable.db import connect_runtime_db
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import get_run, get_run_attempt
from universal_agent.hooks_service import HookAction, HooksService
from universal_agent.workflow_admission import (
    WorkflowAdmissionDecision,
    WorkflowAdmissionService,
)


class _FakeGateway:
    def __init__(self):
        self.execute_calls = 0

    async def resume_session(self, session_id: str):
        return SimpleNamespace(session_id=session_id, workspace_dir=f"/tmp/{session_id}")

    async def execute(self, session, request):
        self.execute_calls += 1
        yield SimpleNamespace(type="tool_call", data={"name": "Write"})
        yield SimpleNamespace(type="iteration_end", data={"duration_seconds": 1.25, "tool_calls": 1})


@pytest.mark.asyncio
async def test_hook_dispatch_tracks_turn_lifecycle(monkeypatch):
    import universal_agent.hooks_service as hs

    monkeypatch.setattr(hs, "load_ops_config", lambda: {})
    gateway = _FakeGateway()

    admitted: list[tuple[str, str]] = []
    finalized: list[tuple[str, str, str, str | None, dict]] = []
    starts: list[tuple[str, str]] = []
    finishes: list[tuple[str, str]] = []

    async def admit(session_id: str, request):
        admitted.append((session_id, request.user_input))
        return {"decision": "accepted", "turn_id": "turn_hook_1"}

    async def finalize(session_id: str, turn_id: str, status: str, error_message, completion):
        finalized.append((session_id, turn_id, status, error_message, completion or {}))

    service = HooksService(
        gateway,
        turn_admitter=admit,
        turn_finalizer=finalize,
        run_counter_start=lambda sid, src: starts.append((sid, src)),
        run_counter_finish=lambda sid, src: finishes.append((sid, src)),
    )

    action = HookAction(kind="agent", session_key="yt_test_123", message="run hook")
    result = await service._dispatch_action(action)

    assert admitted and admitted[0][0] == "session_hook_yt_test_123"
    assert starts == [("session_hook_yt_test_123", "webhook")]
    assert finishes == [("session_hook_yt_test_123", "webhook")]
    assert gateway.execute_calls == 1
    assert finalized
    assert finalized[0][1] == "turn_hook_1"
    assert finalized[0][2] == "completed"
    assert finalized[0][4].get("tool_calls") == 1
    assert result["decision"] == "accepted"
    assert result["turn_id"] == "turn_hook_1"


@pytest.mark.asyncio
async def test_hook_dispatch_skips_when_turn_not_admitted(monkeypatch):
    import universal_agent.hooks_service as hs

    monkeypatch.setattr(hs, "load_ops_config", lambda: {})
    gateway = _FakeGateway()

    starts: list[tuple[str, str]] = []

    async def admit_busy(session_id: str, request):
        return {"decision": "busy", "turn_id": "turn_busy"}

    service = HooksService(
        gateway,
        turn_admitter=admit_busy,
        run_counter_start=lambda sid, src: starts.append((sid, src)),
    )

    action = HookAction(kind="agent", session_key="yt_test_busy", message="run hook")
    result = await service._dispatch_action(action)

    assert gateway.execute_calls == 0
    assert starts == []
    assert result["decision"] == "busy"


@pytest.mark.asyncio
async def test_dispatch_internal_action_with_admission_returns_structured_result(monkeypatch):
    import universal_agent.hooks_service as hs

    monkeypatch.setattr(hs, "load_ops_config", lambda: {})
    gateway = _FakeGateway()

    async def admit(session_id: str, request):
        return {"decision": "accepted", "turn_id": "turn_hook_direct"}

    service = HooksService(gateway, turn_admitter=admit)
    service.config.enabled = True

    result = await service.dispatch_internal_action_with_admission(
        {
            "kind": "agent",
            "session_key": "agentmail_thd_123",
            "message": "hello from inbox",
        }
    )

    assert result["decision"] == "accepted"
    assert result["turn_id"] == "turn_hook_direct"
    assert gateway.execute_calls == 1


@pytest.mark.asyncio
async def test_dispatch_internal_action_routes_agent_actions_through_background_admission(monkeypatch):
    import universal_agent.hooks_service as hs

    monkeypatch.setattr(hs, "load_ops_config", lambda: {})
    gateway = _FakeGateway()
    service = HooksService(gateway)
    service.config.enabled = True
    service.dispatch_internal_action_background_with_admission = AsyncMock(
        return_value={"decision": "accepted", "reason": "dispatched"}
    )

    ok, reason = await service.dispatch_internal_action(
        {
            "kind": "agent",
            "name": "ManualSimoneHandoff",
            "session_key": "simone_handoff_direct_1",
            "message": "activity_id: ntf_direct_1\nPlease investigate.",
        }
    )

    assert ok is True
    assert reason == "agent"
    service.dispatch_internal_action_background_with_admission.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_internal_action_with_admission_creates_generic_hook_run(tmp_path, monkeypatch):
    import universal_agent.hooks_service as hs

    monkeypatch.setattr(hs, "load_ops_config", lambda: {})
    gateway = _FakeGateway()

    async def admit(session_id: str, request):
        return {"decision": "accepted", "turn_id": "turn_hook_generic"}

    runtime_db_path = str((tmp_path / "runtime_state.db").resolve())
    service = HooksService(gateway, turn_admitter=admit)
    service.config.enabled = True
    service._workflow_admission_service = lambda: WorkflowAdmissionService(runtime_db_path)

    result = await service.dispatch_internal_action_with_admission(
        {
            "kind": "agent",
            "name": "AutoHeartbeatInvestigation",
            "session_key": "simone_heartbeat_ntf_123",
            "message": "activity_id: ntf_123\nPlease investigate heartbeat findings.",
        }
    )

    assert result["decision"] == "accepted"
    assert result["run_id"]
    assert result["attempt_id"]
    assert gateway.execute_calls == 1

    conn = connect_runtime_db(runtime_db_path)
    ensure_schema(conn)
    try:
        run_row = get_run(conn, result["run_id"])
        attempt_row = get_run_attempt(conn, result["attempt_id"])
    finally:
        conn.close()

    assert run_row is not None
    assert attempt_row is not None
    assert str(run_row["run_kind"]) == "heartbeat_investigation_hook"
    assert str(run_row["status"]) == "completed"
    assert str(attempt_row["status"]) == "completed"


@pytest.mark.asyncio
async def test_dispatch_internal_action_with_admission_skips_duplicate_generic_hook(tmp_path, monkeypatch):
    import universal_agent.hooks_service as hs

    monkeypatch.setattr(hs, "load_ops_config", lambda: {})
    gateway = _FakeGateway()

    async def admit(session_id: str, request):
        return {"decision": "accepted", "turn_id": "turn_hook_generic"}

    runtime_db_path = str((tmp_path / "runtime_state.db").resolve())
    service = HooksService(gateway, turn_admitter=admit)
    service.config.enabled = True
    service._workflow_admission_service = lambda: WorkflowAdmissionService(runtime_db_path)

    payload = {
        "kind": "agent",
        "name": "ManualSimoneHandoff",
        "session_key": "simone_handoff_ntf_123",
        "message": "activity_id: ntf_123\nPlease investigate and propose actions.",
    }
    first = await service.dispatch_internal_action_with_admission(payload)
    second = await service.dispatch_internal_action_with_admission(payload)

    assert first["decision"] == "accepted"
    assert second["decision"] == "skipped"
    assert second["reason"] == "existing_completed_run"
    assert gateway.execute_calls == 1


@pytest.mark.asyncio
async def test_dispatch_internal_action_background_with_admission_enqueues_generic_hook(tmp_path, monkeypatch):
    import universal_agent.hooks_service as hs

    monkeypatch.setattr(hs, "load_ops_config", lambda: {})
    gateway = _FakeGateway()

    async def admit(session_id: str, request):
        return {"decision": "accepted", "turn_id": "turn_hook_background"}

    runtime_db_path = str((tmp_path / "runtime_state.db").resolve())
    service = HooksService(gateway, turn_admitter=admit)
    service.config.enabled = True
    service._workflow_admission_service = lambda: WorkflowAdmissionService(runtime_db_path)

    result = await service.dispatch_internal_action_background_with_admission(
        {
            "kind": "agent",
            "name": "AutoHeartbeatInvestigation",
            "session_key": "simone_heartbeat_ntf_456",
            "message": "activity_id: ntf_456\nPlease investigate heartbeat findings.",
        }
    )

    assert result["decision"] == "accepted"
    assert result["run_id"]
    assert result["attempt_id"]

    await asyncio.sleep(0.05)

    assert gateway.execute_calls == 1


@pytest.mark.asyncio
async def test_dispatch_internal_action_background_with_admission_returns_retryable_when_runtime_db_locked(tmp_path, monkeypatch):
    import universal_agent.hooks_service as hs

    monkeypatch.setattr(hs, "load_ops_config", lambda: {})
    gateway = _FakeGateway()
    service = HooksService(gateway)
    service.config.enabled = True

    class _LockedAdmissionService:
        def admit(self, *args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

    service._workflow_admission_service = lambda: _LockedAdmissionService()
    service._workflow_admission_retry_base_seconds = 0.01
    service._workflow_admission_retry_max_delay_seconds = 0.02
    service._workflow_admission_retry_ceiling_seconds = 0.03

    result = await service.dispatch_internal_action_background_with_admission(
        {
            "kind": "agent",
            "name": "AutoHeartbeatInvestigation",
            "session_key": "simone_heartbeat_ntf_lock",
            "message": "activity_id: ntf_lock\nPlease investigate heartbeat findings.",
        }
    )

    assert result["decision"] == "failed"
    assert result["reason"] == "runtime_db_locked"
    assert result["retryable"] is True


@pytest.mark.asyncio
async def test_background_admission_serializes_concurrent_workflow_admissions(monkeypatch):
    import universal_agent.hooks_service as hs

    monkeypatch.setattr(hs, "load_ops_config", lambda: {})
    gateway = _FakeGateway()
    service = HooksService(gateway)
    service.config.enabled = True
    service._dispatch_action = AsyncMock(return_value={"decision": "accepted"})
    service._workflow_attempt_context = lambda **_: {"attempt_number": 1, "workspace_dir": "/tmp/workflow"}

    class _SerialAdmissionService:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.calls = 0

        def admit(self, *args, **kwargs):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls += 1
            try:
                time.sleep(0.05)
                return WorkflowAdmissionDecision(
                    "start_new_run",
                    f"run_{self.calls}",
                    f"attempt_{self.calls}",
                    "new_run_created",
                )
            finally:
                self.active -= 1

    admission_service = _SerialAdmissionService()
    service._workflow_admission_service = lambda: admission_service

    first_payload = {
        "kind": "agent",
        "name": "AutoHeartbeatInvestigation",
        "session_key": "simone_heartbeat_ntf_serial_1",
        "message": "activity_id: ntf_serial_1\nPlease investigate heartbeat findings.",
    }
    second_payload = {
        "kind": "agent",
        "name": "AutoHeartbeatInvestigation",
        "session_key": "simone_heartbeat_ntf_serial_2",
        "message": "activity_id: ntf_serial_2\nPlease investigate heartbeat findings.",
    }

    first, second = await asyncio.gather(
        service.dispatch_internal_action_background_with_admission(first_payload),
        service.dispatch_internal_action_background_with_admission(second_payload),
    )
    await asyncio.sleep(0)

    assert first["decision"] == "accepted"
    assert second["decision"] == "accepted"
    assert admission_service.max_active == 1
    assert service._dispatch_action.await_count == 2
