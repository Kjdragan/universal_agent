"""End-to-end smoke for the centralized viewer (Track B Commit 8).

Verifies the resolver contract that producers depend on:
  - POST /api/viewer/resolve takes any of the four input shapes
  - The resolved target carries the correct {session_id, run_id, workspace_dir}
  - Run-only resolution works (the case that was broken before Track B)
  - Daemon session resolution works via the workspace glob fallback

The earlier `/api/viewer/hydrate` endpoint and the parallel
`/dashboard/viewer/...` route were removed; rehydration now happens
client-side in `app/page.tsx` via `extractHistoryFromTraceJson`. The
canonical post-resolve URL is `/?session_id=...&run_id=...`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip if FastAPI deps aren't installed (matches other UA test suites).
fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(tmp_path / "WS"))
    (tmp_path / "WS").mkdir()

    from universal_agent.viewer import resolver as resolver_mod

    class FakeCatalog:
        def __init__(self):
            self.runs: dict = {}
            self.workspace_index: dict = {}
            self.session_index: dict = {}

        def add(self, run_id: str, workspace_dir: str, *, session_id: str | None = None):
            run = {"run_id": run_id, "workspace_dir": workspace_dir,
                   "provider_session_id": session_id}
            self.runs[run_id] = run
            self.workspace_index[workspace_dir] = run
            if session_id:
                self.session_index[session_id] = run
            return run

        def get_run(self, rid):
            return self.runs.get(str(rid or "").strip())

        def find_run_for_workspace(self, ws):
            return self.workspace_index.get(str(ws or "").strip())

        def find_latest_run_for_provider_session(self, sid):
            return self.session_index.get(str(sid or "").strip())

    fake = FakeCatalog()
    monkeypatch.setattr(resolver_mod, "_get_run_catalog", lambda: fake)

    from fastapi import FastAPI

    from universal_agent.api.viewer_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app), fake, tmp_path / "WS"


# ── Run-only resolve (the case that was broken before Track B) ───────────────


def test_run_only_resolve(client):
    test_client, fake, ws_root = client

    ws = ws_root / "run_archived_xyz"
    ws.mkdir()
    fake.add("run_archived_xyz", str(ws))

    res = test_client.post("/api/viewer/resolve", json={"run_id": "run_archived_xyz"})
    assert res.status_code == 200
    target = res.json()
    assert target["target_kind"] == "run"
    assert target["target_id"] == "run_archived_xyz"
    assert target["run_id"] == "run_archived_xyz"
    assert target["workspace_dir"] == str(ws)


# ── Daemon session resolve via glob fallback ─────────────────────────────────


def test_daemon_session_resolve(client):
    test_client, _, ws_root = client

    daemon_ws = ws_root / "run_daemon_simone_todo_001"
    daemon_ws.mkdir()
    (daemon_ws / "active_run_workspace").touch()

    res = test_client.post(
        "/api/viewer/resolve", json={"session_id": "daemon_simone_todo"}
    )
    assert res.status_code == 200
    target = res.json()
    assert target["session_id"] == "daemon_simone_todo"
    assert target["target_kind"] == "session"
    assert target["is_live_session"] is True


# ── 404 / 400 contracts ─────────────────────────────────────────────────────


def test_resolve_404_for_unknown_run(client):
    test_client, _, _ = client
    res = test_client.post("/api/viewer/resolve", json={"run_id": "run_does_not_exist"})
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "viewer_target_not_found"


def test_resolve_400_when_no_inputs(client):
    test_client, _, _ = client
    res = test_client.post("/api/viewer/resolve", json={})
    assert res.status_code == 400


# ── Resolve produces canonical session_id + run_id pair ─────────────────────


def test_resolve_returns_session_id_and_run_id(client):
    """openViewer.ts builds `/?session_id=<id>&run_id=<id>` from these two
    fields. Both must be populated when the catalog has a provider mapping.
    """
    test_client, fake, ws_root = client

    ws = ws_root / "ws_for_session"
    ws.mkdir()
    fake.add("run_for_session", str(ws), session_id="vp_atlas_001")

    res = test_client.post(
        "/api/viewer/resolve", json={"session_id": "vp_atlas_001"}
    )
    assert res.status_code == 200
    target = res.json()
    assert target["session_id"] == "vp_atlas_001"
    assert target["run_id"] == "run_for_session"
    assert target["workspace_dir"] == str(ws)
