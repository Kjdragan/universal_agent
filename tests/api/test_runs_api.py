from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from universal_agent.api import server as api_server
from universal_agent.durable.db import connect_runtime_db
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import create_run_attempt, upsert_run


def test_api_runs_routes_use_local_runtime_db(monkeypatch, tmp_path: Path):
    runtime_db = tmp_path / "runtime_state.db"
    run_dir = tmp_path / "run_local_api_1"
    run_dir.mkdir()

    monkeypatch.delenv("UA_GATEWAY_URL", raising=False)
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(runtime_db.resolve()))

    conn = connect_runtime_db(str(runtime_db.resolve()))
    ensure_schema(conn)
    upsert_run(
        conn,
        run_id="run_api_local_1",
        entrypoint="unit_test",
        run_spec={"workspace_dir": str(run_dir.resolve())},
        status="running",
        workspace_dir=str(run_dir.resolve()),
        run_kind="api_test",
        trigger_source="unit",
    )
    create_run_attempt(
        conn,
        "run_api_local_1",
        attempt_id="attempt_api_local_1",
        status="running",
        workspace_subdir="attempts/001",
    )
    conn.commit()
    conn.close()

    client = TestClient(api_server.app)

    list_resp = client.get("/api/v1/runs")
    assert list_resp.status_code == 200
    list_payload = list_resp.json()
    assert list_payload["runs"]
    assert list_payload["runs"][0]["run_id"] == "run_api_local_1"

    detail_resp = client.get("/api/v1/runs/run_api_local_1")
    assert detail_resp.status_code == 200
    detail_payload = detail_resp.json()
    assert detail_payload["run_id"] == "run_api_local_1"
    assert detail_payload["workspace_dir"] == str(run_dir.resolve())

    attempts_resp = client.get("/api/v1/runs/run_api_local_1/attempts")
    assert attempts_resp.status_code == 200
    attempts_payload = attempts_resp.json()
    assert attempts_payload["run_id"] == "run_api_local_1"
    assert attempts_payload["total"] == 1
    assert attempts_payload["attempts"][0]["attempt_id"] == "attempt_api_local_1"

    (run_dir / "trace.json").write_text('{"ok": true}\n', encoding="utf-8")
    files_resp = client.get("/api/v1/runs/run_api_local_1/files")
    assert files_resp.status_code == 200
    files_payload = files_resp.json()
    assert files_payload["run_id"] == "run_api_local_1"
    assert any(item["name"] == "trace.json" for item in files_payload["files"])

    file_resp = client.get("/api/v1/runs/run_api_local_1/files/trace.json")
    assert file_resp.status_code == 200
    assert file_resp.json() == {"ok": True}


def test_api_runs_routes_proxy_to_gateway(monkeypatch):
    async def _fake_fetch_runs():
        return [{"run_id": "run_gateway_1", "status": "completed"}]

    async def _fake_fetch_run(run_id: str):
        return {"run_id": run_id, "status": "completed", "workspace_dir": "/tmp/run_gateway_1"}

    async def _fake_fetch_run_attempts(run_id: str):
        return [{"attempt_id": f"{run_id}:attempt:1", "run_id": run_id, "status": "completed"}]

    monkeypatch.setattr(api_server, "_gateway_url", lambda: "http://gateway.local")
    monkeypatch.setattr(api_server, "_fetch_gateway_runs", _fake_fetch_runs)
    monkeypatch.setattr(api_server, "_fetch_gateway_run", _fake_fetch_run)
    monkeypatch.setattr(api_server, "_fetch_gateway_run_attempts", _fake_fetch_run_attempts)

    client = TestClient(api_server.app)

    list_resp = client.get("/api/v1/runs")
    assert list_resp.status_code == 200
    assert list_resp.json()["runs"][0]["run_id"] == "run_gateway_1"

    detail_resp = client.get("/api/v1/runs/run_gateway_1")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["workspace_dir"] == "/tmp/run_gateway_1"

    attempts_resp = client.get("/api/v1/runs/run_gateway_1/attempts")
    assert attempts_resp.status_code == 200
    assert attempts_resp.json()["attempts"][0]["attempt_id"] == "run_gateway_1:attempt:1"


def test_api_run_file_routes_not_found_when_run_missing(monkeypatch):
    monkeypatch.delenv("UA_GATEWAY_URL", raising=False)
    monkeypatch.setattr(api_server, "_get_local_run", lambda run_id: None)

    client = TestClient(api_server.app)
    resp = client.get("/api/v1/runs/run_missing/files")
    assert resp.status_code == 404


def test_legacy_api_files_route_accepts_run_id(monkeypatch, tmp_path: Path):
    runtime_db = tmp_path / "runtime_state.db"
    run_dir = tmp_path / "run_local_api_files"
    run_dir.mkdir()
    (run_dir / "trace.json").write_text('{"ok": true}\n', encoding="utf-8")

    monkeypatch.delenv("UA_GATEWAY_URL", raising=False)
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(runtime_db.resolve()))

    conn = connect_runtime_db(str(runtime_db.resolve()))
    ensure_schema(conn)
    upsert_run(
        conn,
        run_id="run_api_files_1",
        entrypoint="unit_test",
        run_spec={"workspace_dir": str(run_dir.resolve())},
        status="completed",
        workspace_dir=str(run_dir.resolve()),
        run_kind="api_test",
        trigger_source="unit",
    )
    conn.commit()
    conn.close()

    client = TestClient(api_server.app)
    resp = client.get("/api/files", params={"run_id": "run_api_files_1"})
    assert resp.status_code == 200
    payload = resp.json()
    assert any(item["name"] == "trace.json" for item in payload["files"])
