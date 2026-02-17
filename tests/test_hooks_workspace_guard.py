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
async def test_write_allows_absolute_artifacts_file_path(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    artifacts = tmp_path / "artifacts"
    workspace.mkdir()
    artifacts.mkdir()

    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(artifacts))

    hooks = AgentHookSet(active_workspace=str(workspace))
    input_data = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(artifacts / "manifest.json"),
            "content": "{}",
        },
    }

    with workspace_context(str(workspace)):
        out = await hooks.on_pre_tool_use_workspace_guard(input_data, tool_use_id="w1", context={})

    assert out == {}


@pytest.mark.asyncio
async def test_write_blocks_absolute_file_path_outside_workspace_and_artifacts(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    artifacts = tmp_path / "artifacts"
    workspace.mkdir()
    artifacts.mkdir()

    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(artifacts))

    hooks = AgentHookSet(active_workspace=str(workspace))
    input_data = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(tmp_path.parent / "escape.txt"),
            "content": "blocked",
        },
    }

    with workspace_context(str(workspace)):
        out = await hooks.on_pre_tool_use_workspace_guard(input_data, tool_use_id="w2", context={})

    assert out.get("decision") == "block"
    assert out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


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


@pytest.mark.asyncio
async def test_pre_bash_auto_cd_workspace_by_default(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.delenv("UA_ARTIFACTS_DIR", raising=False)
    monkeypatch.delenv("UA_BASH_AUTO_CD_WORKSPACE", raising=False)

    hooks = AgentHookSet(active_workspace=str(workspace))
    input_data = {
        "tool_name": "Bash",
        "tool_input": {"command": "python script.py"},
    }

    with workspace_context(str(workspace)):
        out = await hooks.on_pre_bash_inject_workspace_env(input_data, tool_use_id="b2", context={})

    assert "tool_input" in out
    cmd = out["tool_input"]["command"]
    assert f"cd {workspace}" in cmd


@pytest.mark.asyncio
async def test_pre_bash_respects_auto_cd_disable_flag(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("UA_BASH_AUTO_CD_WORKSPACE", "0")
    monkeypatch.delenv("UA_ARTIFACTS_DIR", raising=False)

    hooks = AgentHookSet(active_workspace=str(workspace))
    input_data = {
        "tool_name": "Bash",
        "tool_input": {"command": "python script.py"},
    }

    with workspace_context(str(workspace)):
        out = await hooks.on_pre_bash_inject_workspace_env(input_data, tool_use_id="b3", context={})

    assert "tool_input" in out
    cmd = out["tool_input"]["command"]
    assert f"cd {workspace}" not in cmd


@pytest.mark.asyncio
async def test_pre_bash_rewrites_literal_artifacts_dir_paths(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    artifacts = tmp_path / "artifacts"
    workspace.mkdir()
    artifacts.mkdir()
    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(artifacts))

    hooks = AgentHookSet(active_workspace=str(workspace))
    input_data = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "mkdir -p /opt/universal_agent/UA_ARTIFACTS_DIR/youtube-tutorial-learning/test",
        },
    }

    with workspace_context(str(workspace)):
        out = await hooks.on_pre_bash_inject_workspace_env(input_data, tool_use_id="b4", context={})

    assert "tool_input" in out
    cmd = out["tool_input"]["command"]
    assert "/opt/universal_agent/UA_ARTIFACTS_DIR" not in cmd
    assert str(artifacts) in cmd


@pytest.mark.asyncio
async def test_pre_bash_rewrites_literal_artifacts_dir_paths_top_level_command_shape(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    artifacts = tmp_path / "artifacts"
    workspace.mkdir()
    artifacts.mkdir()
    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(artifacts))

    hooks = AgentHookSet(active_workspace=str(workspace))
    input_data = {
        "tool_name": "Bash",
        "command": "mkdir -p /opt/universal_agent/UA_ARTIFACTS_DIR/youtube-tutorial-learning/test",
    }

    with workspace_context(str(workspace)):
        out = await hooks.on_pre_bash_inject_workspace_env(input_data, tool_use_id="b5", context={})

    assert "command" in out
    cmd = out["command"]
    assert "/opt/universal_agent/UA_ARTIFACTS_DIR" not in cmd
    assert str(artifacts) in cmd


@pytest.mark.asyncio
async def test_heartbeat_investigation_blocks_bash_in_ledger(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("UA_RUN_SOURCE", "heartbeat")
    monkeypatch.setenv("UA_HEARTBEAT_INVESTIGATION_ONLY", "1")

    hooks = AgentHookSet(active_workspace=str(workspace))
    input_data = {
        "tool_name": "Bash",
        "tool_input": {"command": "echo hi"},
    }

    with workspace_context(str(workspace)):
        out = await hooks.on_pre_tool_use_ledger(input_data, tool_use_id="hb1", context={})

    assert out.get("decision") == "block"
    assert out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


@pytest.mark.asyncio
async def test_heartbeat_investigation_write_path_policy(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "work_products").mkdir()
    monkeypatch.setenv("UA_RUN_SOURCE", "heartbeat")
    monkeypatch.setenv("UA_HEARTBEAT_INVESTIGATION_ONLY", "1")

    hooks = AgentHookSet(active_workspace=str(workspace))

    allowed = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(workspace / "work_products" / "draft.md"),
            "content": "draft",
        },
    }
    blocked = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(workspace / "src" / "feature.py"),
            "content": "print('x')",
        },
    }

    with workspace_context(str(workspace)):
        out_allowed = await hooks.on_pre_tool_use_ledger(allowed, tool_use_id="hb2", context={})
        out_blocked = await hooks.on_pre_tool_use_ledger(blocked, tool_use_id="hb3", context={})

    assert out_allowed == {}
    assert out_blocked.get("decision") == "block"
