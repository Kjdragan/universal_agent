from pathlib import Path

from universal_agent.agent_core import UniversalAgent


def test_agent_core_create_workspace_uses_run_prefix(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    agent = UniversalAgent()

    workspace = Path(agent.workspace_dir)
    assert workspace.parent.name == "AGENT_RUN_WORKSPACES"
    assert workspace.name.startswith("run_")
    assert workspace.exists()
