"""HTTP route tests — Track B Commit 1.

Mounts the viewer router on a minimal FastAPI app and exercises:
  - POST /api/viewer/resolve with each input shape (200 / 404 / 400).
  - Response includes the viewer_href and target dict.

The earlier `/api/viewer/hydrate` endpoint was removed; rehydration now
happens client-side in `app/page.tsx` via `extractHistoryFromTraceJson`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(tmp_path / "WS"))
    (tmp_path / "WS").mkdir()

    # Patch the run catalog with an in-memory fake so the routes don't
    # require a live durable DB.
    from universal_agent.viewer import resolver as resolver_mod

    class FakeCatalog:
        def __init__(self):
            self.runs: dict = {}
            self.workspace_index: dict = {}
            self.session_index: dict = {}

        def get_run(self, run_id):
            return self.runs.get(str(run_id or "").strip())

        def find_run_for_workspace(self, ws):
            return self.workspace_index.get(str(ws or "").strip())

        def find_latest_run_for_provider_session(self, session_id):
            return self.session_index.get(str(session_id or "").strip())

    fake = FakeCatalog()
    monkeypatch.setattr(resolver_mod, "_get_run_catalog", lambda: fake)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from universal_agent.api.viewer_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app), fake, tmp_path / "WS"


def test_health(app_client):
    client, _, _ = app_client
    res = client.get("/api/viewer/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["subsystem"] == "viewer"


def test_resolve_400_when_no_inputs(app_client):
    client, _, _ = app_client
    res = client.post("/api/viewer/resolve", json={})
    assert res.status_code == 400


def test_resolve_404_when_unknown(app_client):
    client, _, _ = app_client
    res = client.post("/api/viewer/resolve", json={"run_id": "run_unknown"})
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "viewer_target_not_found"


def test_resolve_by_run_id(app_client):
    client, fake, ws_root = app_client
    ws = ws_root / "run_001_dir"
    ws.mkdir()
    fake.runs["run_001"] = {
        "run_id": "run_001",
        "workspace_dir": str(ws),
        "provider_session_id": None,
    }

    res = client.post("/api/viewer/resolve", json={"run_id": "run_001"})
    assert res.status_code == 200
    body = res.json()
    assert body["target_kind"] == "run"
    assert body["target_id"] == "run_001"
    assert body["viewer_href"] == "/dashboard/viewer/run/run_001"
    assert body["workspace_dir"] == str(ws)


def test_resolve_by_session_id_via_provider(app_client):
    client, fake, ws_root = app_client
    ws = ws_root / "vp_session_run"
    ws.mkdir()
    fake.session_index["vp_atlas_001"] = {
        "run_id": "run_for_atlas",
        "workspace_dir": str(ws),
        "provider_session_id": "vp_atlas_001",
    }

    res = client.post("/api/viewer/resolve", json={"session_id": "vp_atlas_001"})
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == "vp_atlas_001"
    assert body["run_id"] == "run_for_atlas"


def test_resolve_by_daemon_session_id(app_client):
    client, _, ws_root = app_client
    ws = ws_root / "run_daemon_simone_todo_001"
    ws.mkdir()

    res = client.post(
        "/api/viewer/resolve", json={"session_id": "daemon_simone_todo"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == "daemon_simone_todo"
    assert body["target_kind"] == "session"
    assert body["is_live_session"] is True


