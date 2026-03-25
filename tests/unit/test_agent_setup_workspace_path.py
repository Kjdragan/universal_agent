from __future__ import annotations

from pathlib import Path

from universal_agent.agent_setup import create_workspace_path


def test_create_workspace_path_uses_run_prefix(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("AGENT_WORKSPACE_ROOT", raising=False)

    workspace_path = Path(create_workspace_path(str(tmp_path)))

    assert workspace_path.exists()
    assert workspace_path.parent == tmp_path
    assert workspace_path.name.startswith("run_")
