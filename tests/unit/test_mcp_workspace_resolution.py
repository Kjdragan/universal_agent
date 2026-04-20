from __future__ import annotations

import os
from pathlib import Path

import mcp_server


def test_is_valid_session_workspace_accepts_run_workspace(tmp_path: Path):
    run_workspace = tmp_path / "run_123"
    run_workspace.mkdir()

    assert mcp_server._is_valid_session_workspace(str(run_workspace)) is True


def test_resolve_workspace_prefers_current_run_workspace(monkeypatch, tmp_path: Path):
    workspaces_root = tmp_path / "AGENT_RUN_WORKSPACES"
    workspaces_root.mkdir()
    run_workspace = workspaces_root / "run_123"
    run_workspace.mkdir()

    monkeypatch.setattr(mcp_server, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(mcp_server, "_ctx_get_workspace", lambda: None)
    monkeypatch.setenv("CURRENT_RUN_WORKSPACE", str(run_workspace))
    monkeypatch.delenv("CURRENT_SESSION_WORKSPACE", raising=False)
    monkeypatch.delenv("CURRENT_RUN_WORKSPACE_FILE", raising=False)
    monkeypatch.delenv("CURRENT_SESSION_WORKSPACE_FILE", raising=False)

    assert mcp_server._resolve_workspace() == str(run_workspace.resolve())


def test_resolve_workspace_falls_back_to_latest_run_workspace(monkeypatch, tmp_path: Path):
    workspaces_root = tmp_path / "AGENT_RUN_WORKSPACES"
    workspaces_root.mkdir()
    older = workspaces_root / "run_older"
    newer = workspaces_root / "run_newer"
    older.mkdir()
    newer.mkdir()
    (older / "run_manifest.json").write_text("{}", encoding="utf-8")
    (newer / "run_manifest.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(mcp_server, "PROJECT_ROOT", str(tmp_path))
    # DEFAULT_WORKSPACES_ROOT is computed at import-time, so must also be patched
    monkeypatch.setattr(mcp_server, "DEFAULT_WORKSPACES_ROOT", workspaces_root.resolve())
    monkeypatch.setattr(mcp_server, "_ctx_get_workspace", lambda: None)
    monkeypatch.delenv("CURRENT_RUN_WORKSPACE", raising=False)
    monkeypatch.delenv("CURRENT_SESSION_WORKSPACE", raising=False)
    monkeypatch.delenv("CURRENT_RUN_WORKSPACE_FILE", raising=False)
    monkeypatch.delenv("CURRENT_SESSION_WORKSPACE_FILE", raising=False)
    monkeypatch.delenv("UA_WORKSPACES_DIR", raising=False)

    os.utime(older, (1, 1))
    os.utime(newer, None)

    assert mcp_server._resolve_workspace() == str(newer.resolve())
