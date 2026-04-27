import importlib.util
import os
from pathlib import Path

from fastapi.testclient import TestClient

from universal_agent.durable.db import connect_runtime_db
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import upsert_run


def _load_web_server(tmp_path: Path):
    os.environ["UA_RUNTIME_DB_PATH"] = str((tmp_path / "runtime_state.db").resolve())
    module_path = Path(__file__).resolve().parents[2] / "src" / "web" / "server.py"
    spec = importlib.util.spec_from_file_location("ua_legacy_web_server", module_path)
    assert spec is not None and spec.loader is not None
    web_server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(web_server)
    web_server.WORKSPACES_DIR = tmp_path
    return web_server


def test_web_server_lists_run_backed_workspaces(tmp_path: Path):
    web_server = _load_web_server(tmp_path)

    run_dir = tmp_path / "run_20260324_web"
    run_dir.mkdir()

    conn = connect_runtime_db(str((tmp_path / "runtime_state.db").resolve()))
    ensure_schema(conn)
    upsert_run(
        conn,
        run_id="run_web_1",
        entrypoint="unit_test",
        run_spec={"workspace_dir": str(run_dir.resolve())},
        status="completed",
        workspace_dir=str(run_dir.resolve()),
        run_kind="web_test",
        trigger_source="unit",
    )
    conn.commit()
    conn.close()

    client = TestClient(web_server.app)
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["sessions"]
    assert payload["sessions"][0]["session_id"] == "run_20260324_web"
    assert payload["sessions"][0]["run_id"] == "run_web_1"


def test_web_server_lists_and_reads_runs(tmp_path: Path):
    web_server = _load_web_server(tmp_path)

    run_dir = tmp_path / "run_20260324_web"
    run_dir.mkdir()

    conn = connect_runtime_db(str((tmp_path / "runtime_state.db").resolve()))
    ensure_schema(conn)
    upsert_run(
        conn,
        run_id="run_web_2",
        entrypoint="unit_test",
        run_spec={"workspace_dir": str(run_dir.resolve())},
        status="running",
        workspace_dir=str(run_dir.resolve()),
        run_kind="legacy_web",
        trigger_source="unit",
    )
    conn.commit()
    conn.close()

    client = TestClient(web_server.app)

    list_resp = client.get("/api/runs")
    assert list_resp.status_code == 200
    list_payload = list_resp.json()
    assert list_payload["runs"]
    assert list_payload["runs"][0]["run_id"] == "run_web_2"
    assert list_payload["runs"][0]["workspace_dir"] == str(run_dir.resolve())

    detail_resp = client.get("/api/runs/run_web_2")
    assert detail_resp.status_code == 200
    detail_payload = detail_resp.json()
    assert detail_payload["run_id"] == "run_web_2"
    assert detail_payload["status"] == "running"
    assert detail_payload["workspace_dir"] == str(run_dir.resolve())


def test_web_server_run_file_routes(tmp_path: Path):
    web_server = _load_web_server(tmp_path)

    run_dir = tmp_path / "run_20260324_files"
    run_dir.mkdir()
    (run_dir / "trace.json").write_text('{"ok":true}', encoding="utf-8")
    (run_dir / "work_products").mkdir()
    (run_dir / "work_products" / "summary.md").write_text("# summary\n", encoding="utf-8")

    conn = connect_runtime_db(str((tmp_path / "runtime_state.db").resolve()))
    ensure_schema(conn)
    upsert_run(
        conn,
        run_id="run_web_files",
        entrypoint="unit_test",
        run_spec={"workspace_dir": str(run_dir.resolve())},
        status="completed",
        workspace_dir=str(run_dir.resolve()),
        run_kind="legacy_web",
        trigger_source="unit",
    )
    conn.commit()
    conn.close()

    client = TestClient(web_server.app)

    list_resp = client.get("/api/runs/run_web_files/files")
    assert list_resp.status_code == 200
    list_payload = list_resp.json()
    assert list_payload["run_id"] == "run_web_files"
    assert any(item["path"] == "trace.json" for item in list_payload["files"])

    file_resp = client.get("/api/runs/run_web_files/files/trace.json")
    assert file_resp.status_code == 200
    assert file_resp.json() == {"ok": True}

    nested_resp = client.get("/api/runs/run_web_files/files/work_products")
    assert nested_resp.status_code == 200
    nested_payload = nested_resp.json()
    assert nested_payload["run_id"] == "run_web_files"
    assert any(item["path"] == "work_products/summary.md" for item in nested_payload["files"])
