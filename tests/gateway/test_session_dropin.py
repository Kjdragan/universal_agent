import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from universal_agent import gateway_server
from universal_agent.agent_core import AgentEvent, EventType


@pytest.fixture
def client(tmp_path, monkeypatch):
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

    with TestClient(gateway_server.app) as test_client:
        yield test_client


def _create_session(client: TestClient, workspace_dir: Path) -> str:
    response = client.post(
        "/api/v1/sessions",
        json={"user_id": "session_dropin_test", "workspace_dir": str(workspace_dir)},
    )
    assert response.status_code == 200
    return response.json()["session_id"]


def test_idle_attach_receives_stream_on_activity(client, tmp_path, monkeypatch):
    session_id = _create_session(client, tmp_path / "idle_attach")
    gateway = gateway_server.get_gateway()

    async def fake_execute(session, request):
        yield AgentEvent(
            type=EventType.TEXT,
            data={"text": "idle-activity", "author": "Primary Agent", "time_offset": 0.1},
        )

    monkeypatch.setattr(gateway, "execute", fake_execute)

    with client.websocket_connect(f"/api/v1/sessions/{session_id}/stream") as ws:
        connected = ws.receive_json()
        assert connected["type"] == "connected"

        ws.send_json({"type": "execute", "data": {"user_input": "run"}})
        first_event = ws.receive_json()
        assert first_event["type"] == "text"
        assert first_event["data"]["text"] == "idle-activity"
        seen_types = set()
        for _ in range(4):
            evt = ws.receive_json()
            seen_types.add(evt["type"])
            if evt["type"] == "query_complete":
                break
        assert "query_complete" in seen_types


def test_drop_in_to_active_session_streams_tail_events(client, tmp_path, monkeypatch):
    session_id = _create_session(client, tmp_path / "active_dropin")
    gateway = gateway_server.get_gateway()

    async def fake_execute(session, request):
        yield AgentEvent(
            type=EventType.TEXT,
            data={"text": "phase-1", "author": "Primary Agent", "time_offset": 0.1},
        )
        await asyncio.sleep(0.2)
        yield AgentEvent(
            type=EventType.TEXT,
            data={"text": "phase-2", "author": "Primary Agent", "time_offset": 0.2},
        )

    monkeypatch.setattr(gateway, "execute", fake_execute)

    with client.websocket_connect(f"/api/v1/sessions/{session_id}/stream") as ws1:
        assert ws1.receive_json()["type"] == "connected"
        ws1.send_json({"type": "execute", "data": {"user_input": "run"}})
        first = ws1.receive_json()
        assert first["type"] == "text"
        assert first["data"]["text"] == "phase-1"

        with client.websocket_connect(f"/api/v1/sessions/{session_id}/stream") as ws2:
            assert ws2.receive_json()["type"] == "connected"
            ws2_types = set()
            ws2_texts = set()
            for _ in range(5):
                evt = ws2.receive_json()
                ws2_types.add(evt["type"])
                if evt["type"] == "text":
                    ws2_texts.add(evt.get("data", {}).get("text"))
                if evt["type"] == "query_complete":
                    break
            assert "phase-2" in ws2_texts
            assert "query_complete" in ws2_types


def test_switching_sessions_does_not_create_unsolicited_sessions(client, tmp_path):
    session_a = _create_session(client, tmp_path / "alpha")
    session_b = _create_session(client, tmp_path / "beta")

    with client.websocket_connect(f"/api/v1/sessions/{session_a}/stream") as ws_a:
        assert ws_a.receive_json()["type"] == "connected"

    with client.websocket_connect(f"/api/v1/sessions/{session_b}/stream") as ws_b:
        assert ws_b.receive_json()["type"] == "connected"

    sessions_response = client.get("/api/v1/ops/sessions")
    assert sessions_response.status_code == 200
    listed_ids = {item["session_id"] for item in sessions_response.json()["sessions"]}
    assert listed_ids == {"alpha", "beta"}
