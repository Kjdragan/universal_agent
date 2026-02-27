from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from universal_agent.api import server as api_server


def test_storage_session_items_filters_non_session_directories(tmp_path: Path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "downloads").mkdir()

    session_dir = tmp_path / "session_20260221_abc123"
    session_dir.mkdir()
    (session_dir / "run.log").write_text("ok\n", encoding="utf-8")

    vp_dir = tmp_path / "vp_general_primary_external"
    vp_dir.mkdir()

    rows = api_server._storage_session_items("all", 100, include_size=False, root=tmp_path)
    ids = {row["session_id"] for row in rows}

    assert "session_20260221_abc123" in ids
    assert "vp_general_primary_external" in ids
    assert "memory" not in ids
    assert "downloads" not in ids

    vp_row = next(item for item in rows if item["session_id"] == "vp_general_primary_external")
    assert vp_row["source_type"] == "vp"


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
