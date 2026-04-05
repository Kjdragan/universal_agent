from __future__ import annotations

from unittest.mock import MagicMock

from universal_agent import gateway_server
from universal_agent.gateway import GatewaySession


def _session(session_id: str, session_role: str) -> GatewaySession:
    return GatewaySession(
        session_id=session_id,
        user_id="daemon",
        workspace_dir=f"/tmp/{session_id}",
        metadata={"session_role": session_role, "run_kind": session_role},
    )


def test_runtime_registration_routes_sessions_by_role(monkeypatch):
    heartbeat_service = MagicMock()
    todo_dispatch_service = MagicMock()
    monkeypatch.setattr(gateway_server, "_heartbeat_service", heartbeat_service)
    monkeypatch.setattr(gateway_server, "_todo_dispatch_service", todo_dispatch_service)

    heartbeat_session = _session("daemon_simone_heartbeat", "heartbeat")
    todo_session = _session("daemon_simone_todo", "todo_execution")
    triage_session = _session("session_hook_agentmail_1", "email_triage")

    gateway_server._register_session_with_runtime_services(heartbeat_session)
    gateway_server._register_session_with_runtime_services(todo_session)
    gateway_server._register_session_with_runtime_services(triage_session)

    heartbeat_service.register_session.assert_called_once_with(heartbeat_session)
    todo_dispatch_service.register_session.assert_called_once_with(todo_session)


def test_role_filters_match_canonical_runtime_split():
    heartbeat_session = _session("daemon_simone_heartbeat", "heartbeat")
    todo_session = _session("daemon_simone_todo", "todo_execution")
    triage_session = _session("session_hook_agentmail_1", "email_triage")

    assert gateway_server._should_register_with_heartbeat(heartbeat_session) is True
    assert gateway_server._should_register_with_todo_dispatch(heartbeat_session) is False

    assert gateway_server._should_register_with_heartbeat(todo_session) is False
    assert gateway_server._should_register_with_todo_dispatch(todo_session) is True

    assert gateway_server._should_register_with_heartbeat(triage_session) is False
    assert gateway_server._should_register_with_todo_dispatch(triage_session) is False


def test_heartbeat_registration_honors_explicit_skip_flag():
    user_session = GatewaySession(
        session_id="session_chat_direct",
        user_id="user",
        workspace_dir="/tmp/session_chat_direct",
        metadata={"session_role": "user", "run_kind": "user", "skip_heartbeat": True},
    )

    assert gateway_server._should_register_with_heartbeat(user_session) is False
