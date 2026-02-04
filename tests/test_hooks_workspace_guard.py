import pytest

from universal_agent.execution_context import workspace_context
from universal_agent.hooks import AgentHookSet


@pytest.mark.asyncio
async def test_write_text_file_allows_absolute_artifacts_path(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    artifacts = tmp_path / "artifacts"
    workspace.mkdir()
    artifacts.mkdir()

    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(artifacts))

    hooks = AgentHookSet(active_workspace=str(workspace))
    input_data = {
        "tool_name": "mcp__internal__write_text_file",
        "tool_input": {
            "path": str(artifacts / "foo.txt"),
            "content": "hello",
            "overwrite": True,
        },
    }

    with workspace_context(str(workspace)):
        out = await hooks.on_pre_tool_use_workspace_guard(input_data, tool_use_id="t1", context={})

    assert out == {}


@pytest.mark.asyncio
async def test_write_text_file_blocks_escape_outside_workspace_and_artifacts(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    artifacts = tmp_path / "artifacts"
    workspace.mkdir()
    artifacts.mkdir()

    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(artifacts))

    hooks = AgentHookSet(active_workspace=str(workspace))
    input_data = {
        "tool_name": "mcp__internal__write_text_file",
        "tool_input": {
            "path": str(tmp_path.parent / "escape.txt"),
            "content": "nope",
        },
    }

    with workspace_context(str(workspace)):
        out = await hooks.on_pre_tool_use_workspace_guard(input_data, tool_use_id="t2", context={})

    assert out.get("decision") == "block"
    assert out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


@pytest.mark.asyncio
async def test_write_text_file_scopes_relative_path_to_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    artifacts = tmp_path / "artifacts"
    workspace.mkdir()
    artifacts.mkdir()

    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(artifacts))

    hooks = AgentHookSet(active_workspace=str(workspace))
    input_data = {
        "tool_name": "mcp__internal__write_text_file",
        "tool_input": {
            "path": "relative.txt",
            "content": "ok",
        },
    }

    with workspace_context(str(workspace)):
        out = await hooks.on_pre_tool_use_workspace_guard(input_data, tool_use_id="t3", context={})

    assert out.get("tool_input", {}).get("path") == str(workspace / "relative.txt")


@pytest.mark.asyncio
async def test_pre_bash_injects_workspace_and_artifacts(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Ensure UA_ARTIFACTS_DIR isn't present so we exercise default resolution.
    monkeypatch.delenv("UA_ARTIFACTS_DIR", raising=False)

    hooks = AgentHookSet(active_workspace=str(workspace))
    input_data = {
        "tool_name": "Bash",
        "tool_input": {"command": "echo $CURRENT_SESSION_WORKSPACE && echo $UA_ARTIFACTS_DIR"},
    }

    with workspace_context(str(workspace)):
        out = await hooks.on_pre_bash_inject_workspace_env(input_data, tool_use_id="b1", context={})

    assert "tool_input" in out
    cmd = out["tool_input"]["command"]
    assert "export CURRENT_SESSION_WORKSPACE=" in cmd
    assert "export UA_ARTIFACTS_DIR=" in cmd
