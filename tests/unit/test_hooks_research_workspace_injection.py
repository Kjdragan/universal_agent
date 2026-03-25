import asyncio

from universal_agent.hooks import AgentHookSet


def _run(coro):
    return asyncio.run(coro)


def test_research_tool_injects_session_workspace_from_transcript_path(tmp_path):
    session_workspace = tmp_path / "session_20260311_abc12345"
    session_workspace.mkdir()
    (session_workspace / "work_products").mkdir()
    (session_workspace / "session_policy.json").write_text("{}", encoding="utf-8")
    transcript_path = session_workspace / "subagent_outputs" / "research" / "transcript.md"
    transcript_path.parent.mkdir(parents=True)
    transcript_path.write_text("", encoding="utf-8")

    hooks = AgentHookSet(
        run_id="unit-research-ws-injection",
        active_workspace="/opt/universal_agent",
    )

    payload = {
        "tool_name": "mcp__internal__run_research_phase",
        "tool_input": {
            "query": "q",
            "task_name": "t",
        },
        "transcript_path": str(transcript_path),
    }

    result = _run(hooks.on_pre_tool_use_ledger(payload, "tool-1", {}))

    assert result == {}
    assert payload["tool_input"]["workspace_dir"] == str(session_workspace.resolve())


def test_research_tool_does_not_inject_repo_root_workspace(tmp_path):
    hooks = AgentHookSet(
        run_id="unit-research-ws-no-root",
        active_workspace="/opt/universal_agent",
    )

    payload = {
        "tool_name": "mcp__internal__run_research_phase",
        "tool_input": {
            "query": "q",
            "task_name": "t",
        },
    }

    result = _run(hooks.on_pre_tool_use_ledger(payload, "tool-2", {}))

    assert result == {}
    assert "workspace_dir" not in payload["tool_input"]


def test_research_tool_injects_run_workspace_from_transcript_path(tmp_path):
    run_workspace = tmp_path / "run_20260311_abc12345"
    run_workspace.mkdir()
    (run_workspace / "work_products").mkdir()
    (run_workspace / "run_manifest.json").write_text("{}", encoding="utf-8")
    transcript_path = run_workspace / "subagent_outputs" / "research" / "transcript.md"
    transcript_path.parent.mkdir(parents=True)
    transcript_path.write_text("", encoding="utf-8")

    hooks = AgentHookSet(
        run_id="unit-research-run-ws-injection",
        active_workspace="/opt/universal_agent",
    )

    payload = {
        "tool_name": "mcp__internal__run_research_phase",
        "tool_input": {
            "query": "q",
            "task_name": "t",
        },
        "transcript_path": str(transcript_path),
    }

    result = _run(hooks.on_pre_tool_use_ledger(payload, "tool-3", {}))

    assert result == {}
    assert payload["tool_input"]["workspace_dir"] == str(run_workspace.resolve())
