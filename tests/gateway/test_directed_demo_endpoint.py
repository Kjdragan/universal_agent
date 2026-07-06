"""Gateway endpoint tests for the operator-DIRECTED demo lane (S5).

Verifies ``POST /api/v1/directed-demo``:
  * requires ops auth (same ``_require_ops_auth`` gate as every mutating
    dashboard endpoint) — 401 without a token when one is configured;
  * returns 409 when the master flag ``UA_DIRECTED_DEMO_ENABLED`` is off;
  * queues via the intake function and returns the ack shape when enabled.

The intake function itself is unit-tested against a real in-memory Task Hub in
tests/unit/test_directed_demo_builds.py; here it is stubbed so the test isolates
the endpoint's auth + wiring + response contract.
"""

from collections import deque
from contextlib import asynccontextmanager
import time

from fastapi.testclient import TestClient
import pytest

from universal_agent import gateway_server
from universal_agent.gateway import InProcessGateway
from universal_agent.ops_service import OpsService
from universal_agent.runtime_role import build_factory_runtime_policy
from universal_agent.services import directed_demo_builds as ddb


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str((tmp_path / "runtime_state.db").resolve()))
    monkeypatch.setenv("UA_GATEWAY_PORT", "0")

    monkeypatch.setattr(gateway_server, "_gateway", None)
    monkeypatch.setattr(gateway_server, "_ops_service", None)
    monkeypatch.setattr(gateway_server, "_sessions", {})
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


def test_directed_demo_requires_ops_auth(client, monkeypatch):
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "secret-token")
    spy = {"called": False}

    def _stub(*a, **k):
        spy["called"] = True
        return {"status": "queued", "task_id": "directed-build:abc", "slug": "foo"}

    monkeypatch.setattr(ddb, "queue_directed_demo_build", _stub)

    # No auth header → 401, intake never reached.
    r = client.post("/api/v1/directed-demo", json={"seed": "build a demo of foo"})
    assert r.status_code == 401
    assert spy["called"] is False

    # With the ops token → passes auth and queues.
    r2 = client.post(
        "/api/v1/directed-demo",
        json={"seed": "build a demo of foo"},
        headers={"x-ua-ops-token": "secret-token"},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "ok"
    assert body["slug"] == "foo"
    assert body["message"] == "queued: foo (directed lane)"
    assert spy["called"] is True


def test_directed_demo_returns_409_when_lane_disabled(client, monkeypatch):
    # Fail-open auth (no ops token configured), lane OFF → the real intake
    # short-circuits to "disabled" and the endpoint maps that to 409.
    monkeypatch.delenv("UA_DIRECTED_DEMO_ENABLED", raising=False)
    r = client.post("/api/v1/directed-demo", json={"seed": "build a demo of foo"})
    assert r.status_code == 409


def test_directed_demo_rejects_empty_seed(client, monkeypatch):
    monkeypatch.setenv("UA_DIRECTED_DEMO_ENABLED", "1")
    r = client.post("/api/v1/directed-demo", json={"seed": "   "})
    assert r.status_code == 400
