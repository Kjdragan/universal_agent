from __future__ import annotations

from pathlib import Path

from universal_agent.execution_context import get_current_workspace
from universal_agent.vp.clients.claude_cli_client import _build_cli_env


def test_get_current_workspace_prefers_current_run_workspace(monkeypatch, tmp_path: Path):
    run_workspace = tmp_path / "run_123"
    session_workspace = tmp_path / "session_legacy"
    run_workspace.mkdir()
    session_workspace.mkdir()

    monkeypatch.setenv("CURRENT_RUN_WORKSPACE", str(run_workspace))
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", str(session_workspace))

    assert get_current_workspace() == str(run_workspace)


def test_build_cli_env_exports_run_and_session_workspace(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("CURRENT_RUN_WORKSPACE", raising=False)
    monkeypatch.delenv("CURRENT_SESSION_WORKSPACE", raising=False)

    env = _build_cli_env(enable_agent_teams=False, workspace_dir=tmp_path / "run_456")

    expected = str((tmp_path / "run_456"))
    assert env["CURRENT_RUN_WORKSPACE"] == expected
    assert env["CURRENT_SESSION_WORKSPACE"] == expected
