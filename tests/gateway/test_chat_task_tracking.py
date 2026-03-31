from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from universal_agent import gateway_server, task_hub
from universal_agent.agent_core import AgentEvent, EventType
from universal_agent.gateway import GatewaySession


def _db_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


@pytest.fixture
def ws_client(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(gateway_server, "_gateway", None)
    monkeypatch.setattr(gateway_server, "_ops_service", None)
    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_session_runtime", {})
    monkeypatch.setattr(gateway_server, "_pending_gated_requests", {})
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "")
    monkeypatch.setattr(gateway_server, "SESSION_API_TOKEN", "")
    monkeypatch.setattr(gateway_server, "_DEPLOYMENT_PROFILE", "local_workstation")
    monkeypatch.setenv("UA_GATEWAY_PORT", "0")
    monkeypatch.setenv("UA_DISABLE_HEARTBEAT", "1")
    monkeypatch.setenv("UA_DISABLE_CRON", "1")

    async def _allow(_ws):
        return True

    monkeypatch.setattr(gateway_server, "_require_session_ws_auth", _allow)

    with TestClient(gateway_server.app) as test_client:
        yield test_client


def _seed_session(session_id: str, workspace_dir: Path) -> str:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    session = GatewaySession(
        session_id=session_id,
        user_id="chat_task_tracking_test",
        workspace_dir=str(workspace_dir),
        metadata={"source": "user"},
    )
    gateway_server.store_session(session)
    return session_id


def test_prepare_tracked_chat_execution_claims_task_and_builds_prompt(monkeypatch, tmp_path):
    db_path = tmp_path / "activity_state.db"
    monkeypatch.setattr(gateway_server, "_task_hub_open_conn", lambda: _db_connect(db_path))
    monkeypatch.setattr(
        "universal_agent.services.capacity_governor.capacity_snapshot",
        lambda: {"available_slots": 1, "active_slots": 0, "max_concurrent": 2, "in_backoff": False},
    )

    workspace = tmp_path / "chat_workspace"
    workspace.mkdir()
    session = GatewaySession(
        session_id="session_chat_001",
        user_id="owner",
        workspace_dir=str(workspace),
        metadata={"source": "user"},
    )

    tracked = gateway_server._prepare_tracked_chat_execution(
        session=session,
        original_user_input="Research the latest on topic X and summarize it here.",
        turn_id="turn_chat_001",
        client_turn_id="client_turn_001",
    )

    assert tracked["task_id"] == "chat:session_chat_001:turn_chat_001"
    assert tracked["assignment_id"]
    assert tracked["delivery_mode"] == "interactive_chat"
    assert "interactive_chat" in tracked["prompt"]
    assert "Research the latest on topic X and summarize it here." in tracked["prompt"]

    with _db_connect(db_path) as conn:
        item = task_hub.get_item(conn, tracked["task_id"])
        history = task_hub.get_task_history(conn, task_id=tracked["task_id"], limit=5)

    assert item is not None
    assert item["status"] == task_hub.TASK_STATUS_IN_PROGRESS
    assert item["source_kind"] == "chat_panel"
    assert item["metadata"]["delivery_mode"] == "interactive_chat"
    assert history["assignments"][0]["provider_session_id"] == "session_chat_001"


def test_prepare_tracked_chat_execution_honors_explicit_email_delivery(monkeypatch, tmp_path):
    db_path = tmp_path / "activity_state.db"
    monkeypatch.setattr(gateway_server, "_task_hub_open_conn", lambda: _db_connect(db_path))
    monkeypatch.setattr(
        "universal_agent.services.capacity_governor.capacity_snapshot",
        lambda: {"available_slots": 1, "active_slots": 0, "max_concurrent": 2, "in_backoff": False},
    )

    workspace = tmp_path / "email_chat_workspace"
    workspace.mkdir()
    session = GatewaySession(
        session_id="session_chat_002",
        user_id="owner",
        workspace_dir=str(workspace),
        metadata={"source": "user"},
    )

    tracked = gateway_server._prepare_tracked_chat_execution(
        session=session,
        original_user_input="Create a full report on topic Y and email it to me.",
        turn_id="turn_chat_002",
    )

    assert tracked["delivery_mode"] == "standard_report"


def test_gateway_ws_accepts_query_message_alias(ws_client, tmp_path, monkeypatch):
    session_id = _seed_session("query_alias_session", tmp_path / "query_alias")
    gateway = gateway_server.get_gateway()

    async def fake_execute(session, request):
        assert request.user_input == "run alias"
        yield AgentEvent(
            type=EventType.TEXT,
            data={"text": "alias-ok", "author": "Primary Agent", "time_offset": 0.1},
        )

    monkeypatch.setattr(gateway, "execute", fake_execute)

    with ws_client.websocket_connect(f"/api/v1/sessions/{session_id}/stream") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json(
            {
                "type": "query",
                "data": {
                    "text": "run alias",
                    "metadata": {"skip_task_hub_tracking": True},
                },
            }
        )
        first_event = ws.receive_json()
        assert first_event["type"] == "text"
        assert first_event["data"]["text"] == "alias-ok"
        seen_query_complete = False
        for _ in range(4):
            evt = ws.receive_json()
            if evt["type"] == "query_complete":
                seen_query_complete = True
                break
        assert seen_query_complete is True
