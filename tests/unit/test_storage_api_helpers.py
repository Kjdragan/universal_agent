from __future__ import annotations

from pathlib import Path

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
