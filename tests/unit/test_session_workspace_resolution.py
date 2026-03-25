from __future__ import annotations

from pathlib import Path

from universal_agent.utils.session_workspace import (
    resolve_current_run_workspace,
    resolve_current_session_workspace,
)


def test_resolve_current_session_workspace_prefers_run_env(monkeypatch, tmp_path: Path):
    run_workspace = tmp_path / "run_123"
    session_workspace = tmp_path / "session_legacy"
    run_workspace.mkdir()
    session_workspace.mkdir()

    monkeypatch.setenv("CURRENT_RUN_WORKSPACE", str(run_workspace))
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", str(session_workspace))

    assert resolve_current_session_workspace(repo_root=str(tmp_path)) == str(run_workspace)


def test_resolve_current_session_workspace_reads_run_marker(monkeypatch, tmp_path: Path):
    workspaces_root = tmp_path / "AGENT_RUN_WORKSPACES"
    workspaces_root.mkdir()
    run_workspace = workspaces_root / "run_123"
    run_workspace.mkdir()
    marker = workspaces_root / ".current_run_workspace"
    marker.write_text(str(run_workspace), encoding="utf-8")

    monkeypatch.delenv("CURRENT_RUN_WORKSPACE", raising=False)
    monkeypatch.delenv("CURRENT_SESSION_WORKSPACE", raising=False)
    monkeypatch.delenv("CURRENT_RUN_WORKSPACE_FILE", raising=False)
    monkeypatch.delenv("CURRENT_SESSION_WORKSPACE_FILE", raising=False)

    assert resolve_current_session_workspace(repo_root=str(tmp_path)) == str(run_workspace)


def test_resolve_current_run_workspace_alias_matches_legacy_name(monkeypatch, tmp_path: Path):
    run_workspace = tmp_path / "run_456"
    run_workspace.mkdir()

    monkeypatch.setenv("CURRENT_RUN_WORKSPACE", str(run_workspace))
    monkeypatch.delenv("CURRENT_SESSION_WORKSPACE", raising=False)

    assert resolve_current_run_workspace(repo_root=str(tmp_path)) == str(run_workspace)
    assert resolve_current_session_workspace(repo_root=str(tmp_path)) == str(run_workspace)
