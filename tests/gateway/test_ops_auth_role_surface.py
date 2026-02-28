import time
from collections import deque
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from universal_agent import gateway_server
from universal_agent.ops_service import OpsService
from universal_agent.gateway import InProcessGateway
from universal_agent.runtime_role import build_factory_runtime_policy


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str((tmp_path / "runtime_state.db").resolve()))
    monkeypatch.setenv("UA_GATEWAY_PORT", "0")

    monkeypatch.setattr(gateway_server, "_gateway", None)
    monkeypatch.setattr(gateway_server, "_ops_service", None)
    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_session_runtime", {})
    monkeypatch.setattr(gateway_server, "_session_turn_state", {})
    monkeypatch.setattr(gateway_server, "_session_turn_locks", {})
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "_tutorial_bootstrap_jobs", {})
    monkeypatch.setattr(gateway_server, "_tutorial_bootstrap_queue", deque())
    monkeypatch.setattr(gateway_server, "HEARTBEAT_ENABLED", False)
    monkeypatch.setattr(gateway_server, "CRON_ENABLED", False)
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "")
    monkeypatch.setattr(gateway_server, "OPS_JWT_SECRET", "")
    monkeypatch.setattr(gateway_server, "OPS_AUTH_ALLOW_LEGACY", True)
    monkeypatch.setattr(gateway_server, "SESSION_API_TOKEN", "")
    monkeypatch.setattr(gateway_server, "_FACTORY_POLICY", build_factory_runtime_policy("HEADQUARTERS"))
    monkeypatch.setattr(gateway_server, "_scheduling_runtime_started_ts", time.time())

    @asynccontextmanager
    async def _test_lifespan(app):
        gateway_server._gateway = InProcessGateway(workspace_base=tmp_path)
        gateway_server._ops_service = OpsService(gateway_server._gateway, tmp_path)
        yield

    monkeypatch.setattr(gateway_server.app.router, "lifespan_context", _test_lifespan)

    with TestClient(gateway_server.app) as c:
        yield c


def test_local_worker_health_only_blocks_ops_surface(client, monkeypatch):
    monkeypatch.setattr(gateway_server, "_FACTORY_POLICY", build_factory_runtime_policy("LOCAL_WORKER"))

    blocked = client.get("/api/v1/ops/sessions")
    assert blocked.status_code == 403

    health = client.get("/api/v1/health")
    assert health.status_code != 403


def test_issue_ops_token_and_use_bearer(client, monkeypatch):
    monkeypatch.setattr(gateway_server, "_FACTORY_POLICY", build_factory_runtime_policy("HEADQUARTERS"))
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "legacy-bootstrap-token")
    monkeypatch.setattr(gateway_server, "OPS_JWT_SECRET", "jwt-signing-secret-for-tests-32bytes")
    monkeypatch.setattr(gateway_server, "OPS_AUTH_ALLOW_LEGACY", True)

    issue = client.post(
        "/auth/ops-token",
        json={"subject": "worker_integration"},
        headers={"x-ua-ops-token": "legacy-bootstrap-token"},
    )
    assert issue.status_code == 200
    token = issue.json()["token"]
    assert token

    authed = client.get(
        "/api/v1/ops/sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert authed.status_code == 200
    assert "sessions" in authed.json()


def test_ops_surface_unauthorized_is_401_not_500(client, monkeypatch):
    monkeypatch.setattr(gateway_server, "_FACTORY_POLICY", build_factory_runtime_policy("HEADQUARTERS"))
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "required-token")
    monkeypatch.setattr(gateway_server, "OPS_JWT_SECRET", "")
    monkeypatch.setattr(gateway_server, "OPS_AUTH_ALLOW_LEGACY", True)

    resp = client.get("/api/v1/ops/sessions")
    assert resp.status_code == 401
    assert resp.json().get("detail") == "Unauthorized"


def test_local_worker_blocks_websocket_surface(client, monkeypatch):
    monkeypatch.setattr(gateway_server, "_FACTORY_POLICY", build_factory_runtime_policy("LOCAL_WORKER"))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/sessions/session_test/stream"):
            pass
