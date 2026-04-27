from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from universal_agent.api import server as api_server
from universal_agent.durable.db import connect_runtime_db
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import upsert_run


def test_storage_session_items_filters_non_session_directories(tmp_path: Path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "downloads").mkdir()

    session_dir = tmp_path / "session_20260221_abc123"
    session_dir.mkdir()
    (session_dir / "run.log").write_text("ok\n", encoding="utf-8")

    vp_dir = tmp_path / "vp_general_primary_external"
    vp_dir.mkdir()

    run_hook_dir = tmp_path / "run_session_hook_csi_demo"
    run_hook_dir.mkdir()

    rows = api_server._storage_session_items("all", 100, include_size=False, root=tmp_path)
    ids = {row["session_id"] for row in rows}

    assert "session_20260221_abc123" in ids
    assert "vp_general_primary_external" in ids
    assert "run_session_hook_csi_demo" in ids
    assert "memory" not in ids
    assert "downloads" not in ids

    vp_row = next(item for item in rows if item["session_id"] == "vp_general_primary_external")
    assert vp_row["source_type"] == "vp"
    run_hook_row = next(item for item in rows if item["session_id"] == "run_session_hook_csi_demo")
    assert run_hook_row["source_type"] == "hook"


def test_storage_session_items_includes_run_workspace_from_catalog(monkeypatch, tmp_path: Path):
    runtime_db = tmp_path / "runtime_state.db"
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(runtime_db))

    run_dir = tmp_path / "run_20260324_packet8"
    run_dir.mkdir()

    conn = connect_runtime_db(str(runtime_db))
    ensure_schema(conn)
    upsert_run(
        conn,
        run_id="run_packet8_1",
        entrypoint="unit_test",
        run_spec={"workspace_dir": str(run_dir.resolve())},
        status="completed",
        workspace_dir=str(run_dir.resolve()),
        run_kind="migration_test",
        trigger_source="unit",
    )
    conn.commit()
    conn.close()

    rows = api_server._storage_session_items("all", 100, include_size=False, root=tmp_path)
    row = next(item for item in rows if item["session_id"] == "run_20260324_packet8")

    assert row["run_id"] == "run_packet8_1"
    assert row["run_kind"] == "migration_test"
    assert row["trigger_source"] == "unit"
    assert row["status"] == "completed"


def test_storage_run_items_aliases_session_items(monkeypatch, tmp_path: Path):
    runtime_db = tmp_path / "runtime_state.db"
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(runtime_db))

    run_dir = tmp_path / "run_20260324_packet10"
    run_dir.mkdir()

    conn = connect_runtime_db(str(runtime_db))
    ensure_schema(conn)
    upsert_run(
        conn,
        run_id="run_packet10_1",
        entrypoint="unit_test",
        run_spec={"workspace_dir": str(run_dir.resolve())},
        status="completed",
        workspace_dir=str(run_dir.resolve()),
        run_kind="migration_test",
        trigger_source="unit",
    )
    conn.commit()
    conn.close()

    rows = api_server._storage_run_items("all", 100, include_size=False, root=tmp_path)
    row = next(item for item in rows if item["session_id"] == "run_20260324_packet10")

    assert row["run_id"] == "run_packet10_1"
    assert row["run_kind"] == "migration_test"


def test_vps_storage_runs_route_returns_canonical_runs_and_legacy_alias(monkeypatch, tmp_path: Path):
    runtime_db = tmp_path / "runtime_state.db"
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(runtime_db))
    monkeypatch.setattr(api_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(api_server, "VPS_WORKSPACES_MIRROR_DIR", tmp_path)

    run_dir = tmp_path / "run_20260324_packet10_route"
    run_dir.mkdir()

    conn = connect_runtime_db(str(runtime_db))
    ensure_schema(conn)
    upsert_run(
        conn,
        run_id="run_packet10_route",
        entrypoint="unit_test",
        run_spec={"workspace_dir": str(run_dir.resolve())},
        status="completed",
        workspace_dir=str(run_dir.resolve()),
        run_kind="migration_test",
        trigger_source="unit",
    )
    conn.commit()
    conn.close()

    client = TestClient(api_server.app)
    response = client.get("/api/vps/storage/runs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runs"][0]["run_id"] == "run_packet10_route"
    assert payload["sessions"][0]["run_id"] == "run_packet10_route"


def test_vps_storage_overview_exposes_latest_runs_alias(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(api_server, "VPS_WORKSPACES_MIRROR_DIR", tmp_path)
    monkeypatch.setattr(api_server, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(api_server, "VPS_ARTIFACTS_MIRROR_DIR", tmp_path / "artifacts")
    async def _fake_probe():
        return {
            "ok": True,
            "sync_state": "in_sync",
            "pending_ready_count": 0,
            "latest_ready_remote_epoch": None,
            "latest_ready_local_epoch": None,
            "lag_seconds": None,
        }

    monkeypatch.setattr(api_server, "_run_vps_sync_status_probe", _fake_probe)

    run_dir = tmp_path / "run_20260324_overview"
    run_dir.mkdir()

    client = TestClient(api_server.app)
    response = client.get("/api/vps/storage/overview")

    assert response.status_code == 200
    payload = response.json()
    assert "latest_runs" in payload
    assert payload["latest_runs"] == payload["latest_sessions"]


def test_delete_paths_from_root_deletes_files_and_directories(tmp_path: Path):
    (tmp_path / "session_x").mkdir()
    (tmp_path / "session_x" / "run.log").write_text("log\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")

    result = api_server._delete_paths_from_root(tmp_path, ["session_x", "notes.txt", "missing_dir"])

    assert result["deleted_count"] == 2
    assert result["error_count"] == 0
    assert result["skipped_count"] == 1
    assert not (tmp_path / "session_x").exists()
    assert not (tmp_path / "notes.txt").exists()


def test_delete_paths_from_root_blocks_protected_runtime_db_files_by_default(tmp_path: Path):
    db_file = tmp_path / "runtime_state.db"
    db_file.write_text("sqlite-bytes\n", encoding="utf-8")

    result = api_server._delete_paths_from_root(tmp_path, ["runtime_state.db"])

    assert result["deleted_count"] == 0
    assert result["error_count"] == 1
    assert result["protected_blocked_count"] == 1
    assert db_file.exists()
    assert result["errors"][0]["code"] == "protected_requires_override"


def test_delete_paths_from_root_allows_protected_runtime_db_files_with_override(tmp_path: Path):
    db_file = tmp_path / "vp_state.db"
    db_file.write_text("sqlite-bytes\n", encoding="utf-8")

    result = api_server._delete_paths_from_root(tmp_path, ["vp_state.db"], allow_protected=True)

    assert result["deleted_count"] == 1
    assert result["error_count"] == 0
    assert result["protected_blocked_count"] == 0
    assert not db_file.exists()


def test_delete_paths_from_root_blocks_directories_containing_protected_db_files(tmp_path: Path):
    session_dir = tmp_path / "session_x"
    session_dir.mkdir()
    (session_dir / "run.log").write_text("log\n", encoding="utf-8")
    (session_dir / "runtime_state.db").write_text("sqlite-bytes\n", encoding="utf-8")

    result = api_server._delete_paths_from_root(tmp_path, ["session_x"])

    assert result["deleted_count"] == 0
    assert result["error_count"] == 1
    assert result["protected_blocked_count"] == 1
    assert session_dir.exists()
    assert result["errors"][0]["code"] == "protected_requires_override"


def test_storage_root_resolves_local_and_mirror(monkeypatch, tmp_path: Path):
    local_ws = tmp_path / "local_ws"
    mirror_ws = tmp_path / "mirror_ws"
    local_artifacts = tmp_path / "local_artifacts"
    mirror_artifacts = tmp_path / "mirror_artifacts"

    monkeypatch.setattr(api_server, "WORKSPACES_DIR", local_ws)
    monkeypatch.setattr(api_server, "VPS_WORKSPACES_MIRROR_DIR", mirror_ws)
    monkeypatch.setattr(api_server, "ARTIFACTS_DIR", local_artifacts)
    monkeypatch.setattr(api_server, "VPS_ARTIFACTS_MIRROR_DIR", mirror_artifacts)

    assert api_server._storage_root("workspaces", "local") == local_ws
    assert api_server._storage_root("workspaces", "mirror") == mirror_ws
    assert api_server._storage_root("artifacts", "local") == local_artifacts
    assert api_server._storage_root("artifacts", "mirror") == mirror_artifacts


def test_system_session_owner_detection():
    assert api_server._is_system_session_owner("webhook") is True
    assert api_server._is_system_session_owner("cron:hourly") is True
    assert api_server._is_system_session_owner("worker_alpha") is True
    assert api_server._is_system_session_owner("vp.coder.primary") is True
    assert api_server._is_system_session_owner("owner_primary") is False


@pytest.mark.asyncio
async def test_enforce_session_owner_allows_system_owner_mismatch(monkeypatch):
    monkeypatch.setattr(api_server, "_gateway_url", lambda: "http://gateway.local")

    async def _fake_fetch_owner(_session_id: str) -> str:
        return "webhook"

    monkeypatch.setattr(api_server, "_fetch_gateway_session_owner", _fake_fetch_owner)
    await api_server._enforce_session_owner("session_hook_yt_test", "owner_primary", True)


@pytest.mark.asyncio
async def test_enforce_session_owner_blocks_non_system_owner_mismatch(monkeypatch):
    monkeypatch.setattr(api_server, "_gateway_url", lambda: "http://gateway.local")

    async def _fake_fetch_owner(_session_id: str) -> str:
        return "owner_other"

    monkeypatch.setattr(api_server, "_fetch_gateway_session_owner", _fake_fetch_owner)

    with pytest.raises(HTTPException) as exc:
        await api_server._enforce_session_owner("session_custom", "owner_primary", True)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_enforce_session_owner_allows_hook_session_for_primary_owner(monkeypatch):
    monkeypatch.setattr(api_server, "_gateway_url", lambda: "http://gateway.local")
    monkeypatch.setattr(api_server, "_normalize_owner_id", lambda _value=None: "owner_primary")

    async def _fake_fetch_owner(_session_id: str) -> str:
        return "pg-test-8c18facc-7f25-4693-918c-7252c15d36b2"

    monkeypatch.setattr(api_server, "_fetch_gateway_session_owner", _fake_fetch_owner)
    await api_server._enforce_session_owner(
        "session_hook_yt_UCc98QQw1D-y38wg6mO3w4MQ_NAWKFRaR0Sk",
        "owner_primary",
        True,
    )


@pytest.mark.asyncio
async def test_enforce_session_owner_allows_run_hook_session_for_primary_owner(monkeypatch):
    monkeypatch.setattr(api_server, "_gateway_url", lambda: "http://gateway.local")
    monkeypatch.setattr(api_server, "_normalize_owner_id", lambda _value=None: "owner_primary")

    async def _fake_fetch_owner(_session_id: str) -> str:
        return "pg-test-8c18facc-7f25-4693-918c-7252c15d36b2"

    monkeypatch.setattr(api_server, "_fetch_gateway_session_owner", _fake_fetch_owner)
    await api_server._enforce_session_owner(
        "run_session_hook_yt_UCc98QQw1D-y38wg6mO3w4MQ_NAWKFRaR0Sk",
        "owner_primary",
        True,
    )
