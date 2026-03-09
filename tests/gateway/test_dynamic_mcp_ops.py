from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from universal_agent import gateway_server


class _FakeGateway:
    def __init__(self):
        self.add_calls = []
        self.remove_calls = []

    async def get_session_mcp_status(self, session_id: str):
        return {"mcpServers": [{"name": "internal", "status": "connected"}], "session_id": session_id}

    async def add_session_mcp_server(self, session_id: str, server_name: str, server_config: dict):
        self.add_calls.append((session_id, server_name, server_config))
        return {"server_name": server_name, "configured": True, "status": {"mcpServers": []}}

    async def remove_session_mcp_server(self, session_id: str, server_name: str):
        self.remove_calls.append((session_id, server_name))
        return {"server_name": server_name, "removed": True, "status": {"mcpServers": []}}


def _make_client(monkeypatch, fake_gateway: _FakeGateway) -> TestClient:
    @asynccontextmanager
    async def _test_lifespan(_app):
        yield

    monkeypatch.setattr(gateway_server.app.router, "lifespan_context", _test_lifespan)
    monkeypatch.setattr(gateway_server, "_require_ops_auth", lambda _request: None)
    monkeypatch.setattr(gateway_server, "_sanitize_session_id_or_400", lambda value: value)
    monkeypatch.setattr(gateway_server, "get_gateway", lambda: fake_gateway)
    return TestClient(gateway_server.app)


def test_dynamic_mcp_flag_disabled(monkeypatch):
    fake_gateway = _FakeGateway()
    monkeypatch.setenv("UA_ENABLE_DYNAMIC_MCP", "0")
    client = _make_client(monkeypatch, fake_gateway)
    resp = client.get("/api/v1/ops/sessions/session_1/mcp")
    assert resp.status_code == 404


def test_dynamic_mcp_invalid_config_rejected(monkeypatch):
    fake_gateway = _FakeGateway()
    monkeypatch.setenv("UA_ENABLE_DYNAMIC_MCP", "1")
    client = _make_client(monkeypatch, fake_gateway)
    resp = client.post(
        "/api/v1/ops/sessions/session_1/mcp",
        json={"server_name": "bad-server", "server_config": {"type": "invalid"}},
    )
    assert resp.status_code == 400
    assert "not allowed" in resp.json()["detail"]


def test_dynamic_mcp_add_remove_and_status(monkeypatch):
    fake_gateway = _FakeGateway()
    monkeypatch.setenv("UA_ENABLE_DYNAMIC_MCP", "1")
    client = _make_client(monkeypatch, fake_gateway)

    status_resp = client.get("/api/v1/ops/sessions/session_1/mcp")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"]["mcpServers"][0]["name"] == "internal"

    add_resp = client.post(
        "/api/v1/ops/sessions/session_1/mcp",
        json={
            "server_name": "test_stdio",
            "server_config": {"type": "stdio", "command": "echo", "args": ["ok"]},
        },
    )
    assert add_resp.status_code == 200
    assert fake_gateway.add_calls
    assert fake_gateway.add_calls[0][1] == "test_stdio"

    remove_resp = client.request(
        "DELETE",
        "/api/v1/ops/sessions/session_1/mcp",
        json={"server_name": "test_stdio"},
    )
    assert remove_resp.status_code == 200
    assert fake_gateway.remove_calls
    assert fake_gateway.remove_calls[0][1] == "test_stdio"
