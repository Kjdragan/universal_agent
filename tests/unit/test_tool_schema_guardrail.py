import json

import pytest

from universal_agent.guardrails.tool_schema import ToolSchema, pre_tool_use_schema_guardrail


@pytest.mark.anyio
async def test_schema_guardrail_allows_task_and_bash_case_insensitive():
    for tool_name in ("Task", "Bash", "task", "bash"):
        result = await pre_tool_use_schema_guardrail(
            {"tool_name": tool_name, "tool_input": {}},
            run_id="run-test",
            step_id="step-test",
        )
        assert result == {}


@pytest.mark.anyio
async def test_schema_guardrail_blocks_crontab_mutation_for_bash():
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "(crontab -l 2>/dev/null; echo '* * * * * /tmp/x') | crontab -"},
        },
        run_id="run-test",
        step_id="step-test",
    )
    assert result.get("decision") == "block"
    assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    reason = result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    assert "crontab" in reason.lower()


@pytest.mark.anyio
async def test_schema_guardrail_allows_crontab_list_for_bash():
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "crontab -l"},
        },
        run_id="run-test",
        step_id="step-test",
    )
    assert result == {}


@pytest.mark.anyio
async def test_schema_guardrail_blocks_misrouted_system_config_task():
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "Task",
            "tool_input": {
                "subagent_type": "research-specialist",
                "prompt": "Create a chron job that runs every 30 minutes and send heartbeat updates.",
            },
        },
        run_id="run-test",
        step_id="step-test",
    )
    assert result.get("decision") == "block"
    assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    reason = result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    assert "system-configuration-agent" in reason or "System configuration" in reason


@pytest.mark.anyio
async def test_schema_guardrail_allows_non_system_config_task_routing():
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "Task",
            "tool_input": {
                "subagent_type": "research-specialist",
                "prompt": "Research latest robotics funding announcements from the last 7 days.",
            },
        },
        run_id="run-test",
        step_id="step-test",
    )
    # This route may still be normalized for date windows, but should not be blocked.
    assert result.get("decision") != "block"


@pytest.mark.anyio
async def test_schema_guardrail_allows_architecture_diagram_task_even_if_it_mentions_cron():
    # Regression: the system-config misrouting guardrail should not block documentation tasks
    # that merely reference ops concepts like cron/heartbeat.
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "Task",
            "tool_input": {
                "subagent_type": "mermaid-expert",
                "prompt": (
                    "Create a Mermaid diagram showing the system architecture.\n\n"
                    "Include a 'System Ops' section (Cron scheduling, monitoring) and a heartbeat note.\n"
                    "Save to work_products/diagrams/architecture_diagram.mmd."
                ),
            },
        },
        run_id="run-test",
        step_id="step-test",
    )
    assert result.get("decision") != "block"


@pytest.mark.anyio
async def test_schema_guardrail_blocks_malformed_tool_name():
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOLtools</arg_key><arg_value>[]</arg_value>",
            "tool_input": {},
        },
        run_id="run-test",
        step_id="step-test",
    )
    assert result.get("decision") == "block"
    assert (
        result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    )


@pytest.mark.anyio
async def test_schema_guardrail_normalizes_multi_execute_tools_string():
    tools_json = json.dumps(
        [{"tool_slug": "COMPOSIO_SEARCH_NEWS", "arguments": {"query": "ai"}}]
    )
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
            "tool_input": {"tools": tools_json},
        },
        run_id="run-test",
        step_id="step-test",
    )
    updated = result.get("hookSpecificOutput", {}).get("updatedInput")
    assert isinstance(updated, dict)
    assert isinstance(updated.get("tools"), list)


@pytest.mark.anyio
async def test_schema_guardrail_normalizes_multi_execute_arguments_wrapper():
    wrapped = json.dumps(
        {
            "session_id": "abc123",
            "tools": [{"tool_slug": "COMPOSIO_SEARCH_NEWS", "arguments": {"query": "ai"}}],
        }
    )
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
            "tool_input": {"arguments": wrapped},
        },
        run_id="run-test",
        step_id="step-test",
    )
    updated = result.get("hookSpecificOutput", {}).get("updatedInput")
    assert isinstance(updated, dict)
    assert updated.get("session_id") == "abc123"
    assert isinstance(updated.get("tools"), list)


@pytest.mark.anyio
async def test_schema_guardrail_blocks_multi_execute_invalid_tools_type():
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
            "tool_input": {"tools": "not valid json"},
        },
        run_id="run-test",
        step_id="step-test",
    )
    assert result.get("decision") == "block"
    assert (
        result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    )


@pytest.mark.anyio
async def test_schema_guardrail_blocks_multi_execute_missing_tool_slug():
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
            "tool_input": {"tools": [{"arguments": {"query": "ai"}}]},
        },
        run_id="run-test",
        step_id="step-test",
    )
    assert result.get("decision") == "block"
    assert (
        result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    )


@pytest.mark.anyio
async def test_schema_guardrail_blocks_multi_execute_with_internal_vp_tool_slug():
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
            "tool_input": {
                "tools": [
                    {
                        "tool_slug": "vp_dispatch_mission",
                        "arguments": {
                            "vp_id": "vp.general.primary",
                            "objective": "Write a poem.",
                        },
                    }
                ]
            },
        },
        run_id="run-test",
        step_id="step-test",
    )
    assert result.get("decision") == "block"
    assert "Do not wrap `vp_*` tools" in result.get("systemMessage", "")


@pytest.mark.anyio
async def test_schema_guardrail_normalizes_internal_task_name(monkeypatch):
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", "/tmp")
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "mcp__internal__run_research_phase",
            "parent_tool_use_id": "parent-1",
            "tool_input": {"query": "latest news", "task_name": "Russia-Ukraine War Jan 2026"},
        },
        run_id="run-test",
        step_id="step-test",
    )
    updated = result.get("hookSpecificOutput", {}).get("updatedInput")
    assert isinstance(updated, dict)
    assert updated.get("task_name") == "russia_ukraine_war_jan_2026"


@pytest.mark.anyio
async def test_schema_guardrail_blocks_primary_run_research_phase_without_inputs(monkeypatch, tmp_path):
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", str(tmp_path))
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "mcp__internal__run_research_phase",
            "tool_input": {"query": "latest news", "task_name": "ai_news"},
        },
        run_id="run-test",
        step_id="step-test",
    )
    assert result.get("decision") == "block"
    assert "Happy path" in result.get("systemMessage", "")


@pytest.mark.anyio
async def test_schema_guardrail_blocks_research_tool_discovery_before_research_phase(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "session_workspace"
    search_dir = workspace / "search_results"
    search_dir.mkdir(parents=True, exist_ok=True)
    (search_dir / "COMPOSIO_SEARCH_WEB_0.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", str(workspace))

    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "Bash",
            "parent_tool_use_id": "research-subagent-turn",
            "tool_input": {
                "command": (
                    'find . -name "run_research_phase*" -o -name "*research*pipeline*" '
                    "2>/dev/null | head -20"
                )
            },
        },
        run_id="run-test",
        step_id="step-test",
    )

    assert result.get("decision") == "block"
    assert "run_research_phase" in result.get("systemMessage", "")


@pytest.mark.anyio
async def test_schema_guardrail_blocks_workspace_scouting_bash_before_research_phase(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "session_workspace"
    search_dir = workspace / "search_results"
    search_dir.mkdir(parents=True, exist_ok=True)
    (search_dir / "COMPOSIO_SEARCH_NEWS_0.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", str(workspace))

    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "Bash",
            "parent_tool_use_id": "research-subagent-turn",
            "tool_input": {
                "command": "pwd",
            },
        },
        run_id="run-test",
        step_id="step-test",
    )

    assert result.get("decision") == "block"
    assert "run_research_phase" in result.get("systemMessage", "")


@pytest.mark.anyio
async def test_schema_guardrail_blocks_workspace_root_listing_before_research_phase(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "session_workspace"
    search_dir = workspace / "search_results"
    search_dir.mkdir(parents=True, exist_ok=True)
    (search_dir / "COMPOSIO_SEARCH_NEWS_0.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", str(workspace))

    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "mcp__internal__list_directory",
            "parent_tool_use_id": "research-subagent-turn",
            "tool_input": {"path": str(workspace)},
        },
        run_id="run-test",
        step_id="step-test",
    )

    assert result.get("decision") == "block"
    assert "run_research_phase" in result.get("systemMessage", "")


@pytest.mark.anyio
async def test_schema_guardrail_detects_subagent_context_from_transcript_path(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "session_workspace"
    search_dir = workspace / "search_results"
    search_dir.mkdir(parents=True, exist_ok=True)
    (search_dir / "COMPOSIO_SEARCH_WEB_0.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", str(workspace))

    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "Bash",
            "transcript_path": str(
                workspace / "subagent_outputs" / "task:abc123" / "transcript.md"
            ),
            "tool_input": {
                "command": "ls -la",
            },
        },
        run_id="run-test",
        step_id="step-test",
    )

    assert result.get("decision") == "block"
    assert "run_research_phase" in result.get("systemMessage", "")


@pytest.mark.anyio
async def test_schema_guardrail_allows_recovery_after_research_phase_attempt(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "session_workspace"
    search_dir = workspace / "search_results"
    search_dir.mkdir(parents=True, exist_ok=True)
    (search_dir / "COMPOSIO_SEARCH_WEB_0.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", str(workspace))

    first = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "mcp__internal__run_research_phase",
            "parent_tool_use_id": "research-subagent-turn",
            "tool_input": {"query": "latest news", "task_name": "ai_news"},
        },
        run_id="run-test-attempt",
        step_id="step-test",
    )
    assert first == {}

    second = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "Bash",
            "parent_tool_use_id": "research-subagent-turn",
            "tool_input": {
                "command": "pwd",
            },
        },
        run_id="run-test-attempt",
        step_id="step-test",
    )
    assert second == {}


@pytest.mark.anyio
async def test_schema_guardrail_allows_search_results_listing_before_research_phase(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "session_workspace"
    search_dir = workspace / "search_results"
    search_dir.mkdir(parents=True, exist_ok=True)
    (search_dir / "COMPOSIO_SEARCH_WEB_0.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", str(workspace))

    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "mcp__internal__list_directory",
            "parent_tool_use_id": "research-subagent-turn",
            "tool_input": {"path": str(search_dir)},
        },
        run_id="run-test",
        step_id="step-test",
    )

    assert result == {}


@pytest.mark.anyio
async def test_schema_guardrail_blocks_composio_search_tools_for_reddit():
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "mcp__composio__COMPOSIO_SEARCH_TOOLS",
            "tool_input": {
                "queries": [
                    {
                        "use_case": "get trending posts on reddit about AI agents",
                        "known_fields": "subreddit: LocalLLaMA",
                    }
                ]
            },
        },
        run_id="run-test",
        step_id="step-test",
    )
    assert result.get("decision") == "block"
    assert "unnecessary for Reddit" in result.get("systemMessage", "")


@pytest.mark.anyio
async def test_schema_guardrail_normalizes_report_generation_inline_corpus(monkeypatch, tmp_path):
    workspace = tmp_path / "session_workspace"
    refined = (
        workspace
        / "tasks"
        / "russia_ukraine_war_jan_2026"
        / "refined_corpus.md"
    )
    refined.parent.mkdir(parents=True, exist_ok=True)
    refined.write_text("# refined corpus\n", encoding="utf-8")
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", str(workspace))
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "mcp__internal__run_report_generation",
            "tool_input": {
                "query": "summary",
                "task_name": "Russia-Ukraine War Jan 2026",
                "corpus_data": "Header\\n" + ("fact\\n" * 120),
            },
        },
        run_id="run-test",
        step_id="step-test",
    )
    updated = result.get("hookSpecificOutput", {}).get("updatedInput")
    assert isinstance(updated, dict)
    assert updated.get("task_name") == "russia_ukraine_war_jan_2026"
    assert updated.get("corpus_data") == str(refined)


@pytest.mark.anyio
async def test_schema_guardrail_normalizes_html_to_pdf_output_name():
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "mcp__internal__html_to_pdf",
            "tool_input": {
                "html_path": "/tmp/report.html",
                "pdf_path": "/tmp/russia-ukraine-war-report.pdf",
            },
        },
        run_id="run-test",
        step_id="step-test",
    )
    updated = result.get("hookSpecificOutput", {}).get("updatedInput")
    assert isinstance(updated, dict)
    assert updated.get("pdf_path") == "/tmp/russia_ukraine_war_report.pdf"


@pytest.mark.anyio
async def test_schema_guardrail_injects_canonical_rolling_window_for_research_task(monkeypatch):
    monkeypatch.setenv("USER_TIMEZONE", "America/Chicago")
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "Task",
            "tool_input": {
                "subagent_type": "research-specialist",
                "prompt": "Research the latest updates from the past 3 days.",
            },
        },
        run_id="run-test",
        step_id="step-test",
    )
    updated = result.get("hookSpecificOutput", {}).get("updatedInput")
    assert isinstance(updated, dict)
    assert "MANDATORY DATE WINDOW:" in updated.get("prompt", "")


@pytest.mark.anyio
async def test_schema_guardrail_rewrites_stale_inline_date_window_for_research_task(monkeypatch):
    monkeypatch.setenv("USER_TIMEZONE", "America/Chicago")
    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "Task",
            "tool_input": {
                "subagent_type": "research-specialist",
                "prompt": (
                    "Research the latest news from the Russia-Ukraine war over the past three days "
                    "(January 30-31, February 1, 2026)."
                ),
            },
        },
        run_id="run-test",
        step_id="step-test",
    )
    updated = result.get("hookSpecificOutput", {}).get("updatedInput")
    assert isinstance(updated, dict)
    updated_prompt = updated.get("prompt", "")
    assert "January 30-31, February 1, 2026" not in updated_prompt
    assert "MANDATORY DATE WINDOW:" in updated_prompt


@pytest.mark.anyio
async def test_schema_guardrail_uses_dynamic_schema_fetcher():
    def fetcher(tool_name: str) -> ToolSchema:
        assert tool_name == "COMPOSIO_FAKE_TOOL"
        return ToolSchema(required=("required_field",), example="COMPOSIO_FAKE_TOOL({\"required_field\": \"...\"})")

    result = await pre_tool_use_schema_guardrail(
        {
            "tool_name": "COMPOSIO_FAKE_TOOL",
            "tool_input": {},
        },
        run_id="run-test",
        step_id="step-test",
        schema_fetcher=fetcher,
    )
    assert result.get("decision") == "block"
