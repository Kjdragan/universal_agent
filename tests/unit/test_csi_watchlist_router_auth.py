"""The CSI watchlist router must be ops-auth gated at the mount.

The router (`api/routers/csi_watchlist.py`) imports no auth helper, and it sits
outside the `/api/v1/ops/*` middleware — so every route (add/delete/patch
channels and categories, plus the reads) was reachable unauthenticated. It is
now mounted with `dependencies=[Depends(_require_ops_auth)]`. This test rebuilds
that exact mount and asserts an unauthenticated request is rejected while a
correctly-tokened one passes the gate.
"""

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import universal_agent.gateway_server as gs
from universal_agent.api.routers.csi_watchlist import router as watchlist_router


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(gs, "OPS_TOKEN", "test-ops-token", raising=False)
    monkeypatch.setattr(gs, "OPS_JWT_SECRET", "", raising=False)
    app = FastAPI()
    app.include_router(
        watchlist_router, dependencies=[Depends(gs._require_ops_auth)]
    )
    return TestClient(app, raise_server_exceptions=False)


def test_watchlist_get_rejected_without_token(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/api/v1/csi/watchlist")
    assert resp.status_code == 401


def test_watchlist_mutations_rejected_without_token(monkeypatch):
    client = _client(monkeypatch)
    assert client.post("/api/v1/csi/watchlist/add", json={}).status_code == 401
    assert client.delete("/api/v1/csi/watchlist/somechannel").status_code == 401


def test_watchlist_passes_gate_with_valid_token(monkeypatch):
    client = _client(monkeypatch)
    # Correct ops token → the gate lets it through (status is whatever the
    # handler returns, just NOT the 401 the gate raises).
    resp = client.get(
        "/api/v1/csi/watchlist", headers={"x-ua-ops-token": "test-ops-token"}
    )
    assert resp.status_code != 401
