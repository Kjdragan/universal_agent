import asyncio
from pathlib import Path

from universal_agent import main as agent_main
from universal_agent.session_ctx import SessionContext, reset_ctx, set_ctx


def _run(coro):
    return asyncio.run(coro)


def _call_pre(tool_name: str, tool_input: dict):
    return _run(
        agent_main.on_pre_tool_use_ledger(
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
            },
            "tool-pre-main",
            {},
        )
    )


def test_main_blocks_taskstop_placeholder_id(tmp_path: Path):
    token = set_ctx(SessionContext(run_id="run-main-taskstop", observer_workspace_dir=str(tmp_path)))
    try:
        result = _call_pre("TaskStop", {"task_id": "all"})
    finally:
        reset_ctx(token)

    assert result.get("decision") == "block"
    assert "Invalid TaskStop request blocked" in str(result.get("systemMessage", ""))


def test_main_blocks_agent_for_pipeline_subagent(tmp_path: Path):
    token = set_ctx(SessionContext(run_id="run-main-agent", observer_workspace_dir=str(tmp_path)))
    try:
        result = _call_pre(
            "Agent",
            {
                "subagent_type": "research-specialist",
                "description": "research task",
                "prompt": "collect sources",
            },
        )
    finally:
        reset_ctx(token)

    assert result.get("decision") == "block"
    assert "Use `Task` for pipeline delegation" in str(result.get("systemMessage", ""))


def test_main_allows_taskstop_with_plausible_id_when_ledger_missing(tmp_path: Path):
    token = set_ctx(SessionContext(run_id="run-main-taskstop-allow", observer_workspace_dir=str(tmp_path)))
    try:
        result = _call_pre("TaskStop", {"task_id": "task_01HZYQ7QF1"})
    finally:
        reset_ctx(token)

    assert result == {}
