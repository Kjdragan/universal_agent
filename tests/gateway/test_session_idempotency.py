import asyncio
import time
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
    monkeypatch.setattr(gateway_server, "_session_turn_state", {})
    monkeypatch.setattr(gateway_server, "_session_turn_locks", {})
    monkeypatch.setenv("UA_GATEWAY_PORT", "0")
    monkeypatch.setenv("UA_DISABLE_HEARTBEAT", "1")
    monkeypatch.setenv("UA_DISABLE_CRON", "1")

    with TestClient(gateway_server.app) as test_client:
        yield test_client


def _create_session(client: TestClient, workspace_dir: Path) -> str:
    response = client.post(
        "/api/v1/sessions",
        json={"user_id": "session_idempotency_test", "workspace_dir": str(workspace_dir)},
    )
    assert response.status_code == 200
    return response.json()["session_id"]


def _receive_until(websocket, predicate, max_messages: int = 12):
    for _ in range(max_messages):
        event = websocket.receive_json()
        if predicate(event):
            return event
    raise AssertionError("Expected websocket event not observed within message budget")


def test_rejects_second_writer_and_duplicate_in_progress(client, tmp_path, monkeypatch):
    session_id = _create_session(client, tmp_path / "writer_lock")
    gateway = gateway_server.get_gateway()

    async def fake_execute(session, request):
        yield AgentEvent(
            type=EventType.TEXT,
            data={"text": "start", "author": "Primary Agent", "time_offset": 0.1},
        )
        await asyncio.sleep(0.25)
        yield AgentEvent(
            type=EventType.TEXT,
            data={"text": "done", "author": "Primary Agent", "time_offset": 0.2},
        )

    monkeypatch.setattr(gateway, "execute", fake_execute)

    with client.websocket_connect(f"/api/v1/sessions/{session_id}/stream") as ws1:
        assert ws1.receive_json()["type"] == "connected"
        with client.websocket_connect(f"/api/v1/sessions/{session_id}/stream") as ws2:
            assert ws2.receive_json()["type"] == "connected"

            ws1.send_json(
                {"type": "execute", "data": {"user_input": "run", "client_turn_id": "turn-A"}}
            )
            _receive_until(ws1, lambda e: e.get("type") == "text")

            ws2.send_json(
                {"type": "execute", "data": {"user_input": "new turn", "client_turn_id": "turn-B"}}
            )
            busy = _receive_until(
                ws2,
                lambda e: e.get("type") == "status"
                and e.get("data", {}).get("status") == "turn_rejected_busy",
            )
            assert busy["data"]["active_turn_id"] == "turn-A"

            ws2.send_json(
                {"type": "execute", "data": {"user_input": "retry same", "client_turn_id": "turn-A"}}
            )
            duplicate_running = _receive_until(
                ws2,
                lambda e: e.get("type") == "status"
                and e.get("data", {}).get("status") == "turn_in_progress",
            )
            assert duplicate_running["data"]["turn_id"] == "turn-A"


def test_duplicate_completed_turn_is_ignored(client, tmp_path, monkeypatch):
    session_id = _create_session(client, tmp_path / "dup_completed")
    gateway = gateway_server.get_gateway()
    execute_calls = {"count": 0}

    async def fake_execute(session, request):
        execute_calls["count"] += 1
        yield AgentEvent(
            type=EventType.TEXT,
            data={"text": "only-run-once", "author": "Primary Agent", "time_offset": 0.1},
        )

    monkeypatch.setattr(gateway, "execute", fake_execute)

    with client.websocket_connect(f"/api/v1/sessions/{session_id}/stream") as ws:
        assert ws.receive_json()["type"] == "connected"

        ws.send_json(
            {"type": "execute", "data": {"user_input": "run", "client_turn_id": "turn-C"}}
        )
        _receive_until(ws, lambda e: e.get("type") == "query_complete")

        ws.send_json(
            {"type": "execute", "data": {"user_input": "run-again", "client_turn_id": "turn-C"}}
        )
        dup = _receive_until(
            ws,
            lambda e: e.get("type") == "status"
            and e.get("data", {}).get("status") == "duplicate_turn_ignored",
        )
        assert dup["data"]["turn_id"] == "turn-C"

        _receive_until(
            ws,
            lambda e: e.get("type") == "query_complete"
            and e.get("data", {}).get("turn_id") == "turn-C",
        )

    assert execute_calls["count"] == 1


def test_fingerprint_dedupe_without_client_turn_id(client, tmp_path, monkeypatch):
    session_id = _create_session(client, tmp_path / "fingerprint_dedupe")
    gateway = gateway_server.get_gateway()
    execute_calls = {"count": 0}

    async def fake_execute(session, request):
        execute_calls["count"] += 1
        yield AgentEvent(
            type=EventType.TEXT,
            data={"text": "fingerprint-once", "author": "Primary Agent", "time_offset": 0.1},
        )

    monkeypatch.setattr(gateway, "execute", fake_execute)

    with client.websocket_connect(f"/api/v1/sessions/{session_id}/stream") as ws:
        assert ws.receive_json()["type"] == "connected"

        ws.send_json({"type": "execute", "data": {"user_input": "repeat-me"}})
        _receive_until(ws, lambda e: e.get("type") == "query_complete")
        time.sleep(0.1)

        ws.send_json({"type": "execute", "data": {"user_input": "repeat-me"}})
        duplicate = _receive_until(
            ws,
            lambda e: e.get("type") == "status"
            and e.get("data", {}).get("status") in {"duplicate_turn_ignored", "turn_in_progress"},
        )
        assert duplicate["data"]["status"] in {"duplicate_turn_ignored", "turn_in_progress"}

    assert execute_calls["count"] == 1
