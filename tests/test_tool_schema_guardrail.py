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
