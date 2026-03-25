import sqlite3
from pathlib import Path

from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import create_run_attempt, upsert_run
from universal_agent.run_catalog import RunCatalogService


def _seed_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    upsert_run(
        conn,
        "run-1",
        "cli",
        {"workspace_dir": str((db_path.parent / "run-1").resolve())},
        workspace_dir=str((db_path.parent / "run-1").resolve()),
        status="running",
        run_kind="heartbeat_investigation",
        trigger_source="heartbeat",
        run_policy="automation_ephemeral",
        external_origin="csi_ingester",
    )
    create_run_attempt(conn, "run-1", status="running")
    conn.close()


def test_run_catalog_lists_and_finds_runs_by_workspace(tmp_path):
    db_path = tmp_path / "runtime_state.db"
    workspace_dir = (tmp_path / "run-1").resolve()
    workspace_dir.mkdir(parents=True)
    _seed_db(db_path)

    catalog = RunCatalogService(str(db_path))

    runs = catalog.list_runs()
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run-1"
    assert runs[0]["workspace_dir"] == str(workspace_dir)
    assert runs[0]["run_kind"] == "heartbeat_investigation"
    assert runs[0]["attempt_count"] == 1

    run = catalog.get_run("run-1")
    assert run is not None
    assert run["trigger_source"] == "heartbeat"
    assert run["external_origin"] == "csi_ingester"

    by_workspace = catalog.find_run_for_workspace(workspace_dir)
    assert by_workspace is not None
    assert by_workspace["run_id"] == "run-1"
