"""End-to-end smoke for the centralized viewer (Track B Commit 8).

Verifies that the contract producers depend on really works:
  - POST /api/viewer/resolve takes any of the four input shapes
  - The resolved viewer_href is well-formed
  - GET /api/viewer/hydrate returns populated panels for that target
  - Every response sanitizes card-data (defense-in-depth)
  - Run-only target (the case that was broken before Track B) works

Replaces the partial coverage scattered across the unit tests with a
single contract assertion that exercises the full HTTP path.
"""

from __future__ import annotations

import json
import os
import sys
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


# ── Run-only end-to-end (the case that was broken before Track B) ────────────


def test_run_only_resolve_then_hydrate(client):
    test_client, fake, ws_root = client

    ws = ws_root / "run_archived_xyz"
    ws.mkdir()
    fake.add("run_archived_xyz", str(ws))

    # Producer step: resolve
    res = test_client.post("/api/viewer/resolve", json={"run_id": "run_archived_xyz"})
    assert res.status_code == 200
    target = res.json()
    assert target["target_kind"] == "run"
    assert target["viewer_href"] == "/dashboard/viewer/run/run_archived_xyz"

    # Add some artifacts the panels will pull
    (ws / "trace.json").write_text(json.dumps({
        "messages": [
            {"role": "user", "content": "what happened in this archived run?"},
            {"role": "assistant", "content": "I executed the workflow"},
        ]
    }))
    (ws / "run.log").write_text("INFO: workflow started\nINFO: workflow completed\n")
    (ws / "run_checkpoint.json").write_text("{}")

    # Viewer step: hydrate using the canonical anchor
    res = test_client.get(
        f"/api/viewer/hydrate?target_kind=run&target_id=run_archived_xyz"
    )
    assert res.status_code == 200
    body = res.json()

    # All three panels populated
    assert len(body["history"]) == 2
    assert body["history"][0]["role"] == "user"
    assert "archived run" in body["history"][0]["content"]
    assert len(body["logs"]) >= 2
    assert body["readiness"]["state"] == "ready"
    assert "run_checkpoint.json" in body["readiness"]["reason"]


# ── Daemon session end-to-end ────────────────────────────────────────────────


def test_daemon_session_resolve_then_hydrate(client):
    test_client, _, ws_root = client

    daemon_ws = ws_root / "run_daemon_simone_todo_001"
    daemon_ws.mkdir()
    (daemon_ws / "active_run_workspace").touch()
    (daemon_ws / "trace.json").write_text(
        json.dumps({"messages": [{"role": "system", "content": "daemon online"}]})
    )

    # Producer step: resolve by session_id (the case that needs the daemon
    # glob fallback in the resolver)
    res = test_client.post(
        "/api/viewer/resolve", json={"session_id": "daemon_simone_todo"}
    )
    assert res.status_code == 200
    target = res.json()
    assert target["session_id"] == "daemon_simone_todo"
    assert target["is_live_session"] is True
    assert target["viewer_href"] == "/dashboard/viewer/session/daemon_simone_todo"

    # Viewer step
    res = test_client.get(
        "/api/viewer/hydrate?target_kind=session&target_id=daemon_simone_todo"
    )
    assert res.status_code == 200
    assert len(res.json()["history"]) == 1


# ── PAN masking contract ────────────────────────────────────────────────────


def test_card_data_never_leaks_through_hydrate(client):
    """Defense-in-depth: card-shaped strings must be masked."""
    test_client, fake, ws_root = client

    ws = ws_root / "run_card_test"
    ws.mkdir()
    fake.add("run_card_test", str(ws))

    (ws / "trace.json").write_text(json.dumps({
        "messages": [
            {"role": "user", "content": "I used 4242 4242 4242 4242 yesterday"},
        ]
    }))
    (ws / "run.log").write_text("INFO: charged 4111111111111234 ok\n")
    (ws / "run_checkpoint.json").write_text("{}")

    res = test_client.get(
        "/api/viewer/hydrate?target_kind=run&target_id=run_card_test"
    )
    body_text = res.text
    assert "4242 4242 4242 4242" not in body_text
    assert "4242424242424242" not in body_text
    assert "4111111111111234" not in body_text
    assert "••••4242" in body_text
    assert "••••1234" in body_text


# ── 404 contract ─────────────────────────────────────────────────────────────


def test_resolve_404_for_unknown_run(client):
    test_client, _, _ = client
    res = test_client.post("/api/viewer/resolve", json={"run_id": "run_does_not_exist"})
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "viewer_target_not_found"


def test_resolve_400_when_no_inputs(client):
    test_client, _, _ = client
    res = test_client.post("/api/viewer/resolve", json={})
    assert res.status_code == 400


def test_hydrate_404_for_unknown_target(client):
    test_client, _, _ = client
    res = test_client.get(
        "/api/viewer/hydrate?target_kind=run&target_id=run_unknown"
    )
    assert res.status_code == 404


# ── viewer_href round-trip (the contract producers depend on) ────────────────


def test_viewer_href_is_consumable_by_route_pattern(client):
    """Producers extract viewer_href and feed it to the new Next.js route.
    The route's segment is /dashboard/viewer/[targetKind]/[targetId], so
    viewer_href must always look like that pattern.
    """
    test_client, fake, ws_root = client

    cases = [
        ("run_x", None, "run", "run_x"),
        ("run_for_session", "vp_atlas_001", "run", "run_for_session"),
    ]
    for run_id, session_id, expected_kind, expected_id in cases:
        ws = ws_root / f"ws_{run_id}"
        ws.mkdir()
        fake.add(run_id, str(ws), session_id=session_id)

        res = test_client.post(
            "/api/viewer/resolve",
            json={"run_id": run_id} if session_id is None else {"session_id": session_id},
        )
        assert res.status_code == 200
        href = res.json()["viewer_href"]
        assert href == f"/dashboard/viewer/{expected_kind}/{expected_id}"
