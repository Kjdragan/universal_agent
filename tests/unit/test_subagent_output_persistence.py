import json
from pathlib import Path

from universal_agent import main as agent_main


def test_subagent_output_persistence(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool_input = {"subagent_type": "report-creation-expert", "prompt": "Hello"}

    paths = agent_main._persist_subagent_output(
        workspace_dir=str(workspace),
        tool_use_id="tool-1",
        tool_input=tool_input,
        raw_tool_name="Task",
        output={"summary": "done"},
        output_str="Subagent finished",
    )
    assert paths is not None
    assert Path(paths["json"]).exists()
    assert Path(paths["summary"]).exists()

    with open(paths["json"], "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["task_key"]
    assert payload["tool_use_id"] == "tool-1"

    assert agent_main._subagent_output_available(
        str(workspace), payload["task_key"]
    ) is True
