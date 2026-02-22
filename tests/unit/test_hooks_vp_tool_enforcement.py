import asyncio

from universal_agent.hooks import AgentHookSet


def _run(coro):
    return asyncio.run(coro)


def test_blocks_task_when_user_prompt_explicitly_requests_general_vp():
    hooks = AgentHookSet(run_id="unit-vp-enforcement")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Hi Simone, use the General VP to write a poem."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "description": "Create a poem via VP",
                    "prompt": "You are the General VP. Create a poem.",
                },
            },
            "tool-1",
            {},
        )
    )

    assert result.get("decision") == "block"
    assert "vp_dispatch_mission" in str(result.get("systemMessage", ""))


def test_blocks_task_when_user_prompt_uses_general_vp_alias():
    hooks = AgentHookSet(run_id="unit-vp-enforcement-general-vp")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Simone, use the general VP to create a poem and email it."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "description": "Create poem",
                    "prompt": "You are the General VP. Write a poem.",
                },
            },
            "tool-general-vp",
            {},
        )
    )

    assert result.get("decision") == "block"
    assert "vp_dispatch_mission" in str(result.get("systemMessage", ""))


def test_blocks_task_when_user_prompt_uses_vp_general_word_order():
    hooks = AgentHookSet(run_id="unit-vp-enforcement-vp-general-order")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Use the VP general agent to write a story and email it."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "description": "Create story",
                    "prompt": "You are the General VP. Write a story.",
                },
            },
            "tool-vp-general-order",
            {},
        )
    )

    assert result.get("decision") == "block"
    assert "vp_dispatch_mission" in str(result.get("systemMessage", ""))


def test_blocks_task_when_payload_tries_general_vp_without_explicit_turn_state():
    hooks = AgentHookSet(run_id="unit-vp-enforcement-payload")
    _run(hooks.on_user_prompt_skill_awareness({"prompt": "Write a poem and email it to me."}))

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "description": "Delegate to General VP",
                    "prompt": "You are the General VP. Create a poem.",
                },
            },
            "tool-2",
            {},
        )
    )

    assert result.get("decision") == "block"

    followup = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "echo fallback"},
            },
            "tool-2b",
            {},
        )
    )
    assert followup.get("decision") == "block"
    assert "First tool call in this turn must be `vp_dispatch_mission" in str(
        followup.get("systemMessage", "")
    )


def test_allows_task_after_vp_dispatch_in_same_turn():
    hooks = AgentHookSet(run_id="unit-vp-enforcement-dispatch")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Use the General VP to produce the result."}
        )
    )

    dispatch_result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "vp_dispatch_mission",
                "tool_input": {"vp_id": "vp.general.primary", "objective": "Create a poem"},
            },
            "tool-dispatch",
            {},
        )
    )
    assert dispatch_result == {}

    task_result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "description": "Fallback path",
                    "prompt": "regular delegation",
                },
            },
            "tool-task",
            {},
        )
    )
    assert task_result == {}


def test_blocks_non_vp_tool_before_dispatch_when_prompt_has_explicit_vp_intent():
    hooks = AgentHookSet(run_id="unit-vp-enforcement-pre-dispatch")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Use the General VP to create a poem and email it."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "src/universal_agent/vp/profiles.py"},
            },
            "tool-read-before-dispatch",
            {},
        )
    )

    assert result.get("decision") == "block"
    assert "First tool call" in str(result.get("systemMessage", ""))


def test_allows_task_when_no_explicit_vp_intent():
    hooks = AgentHookSet(run_id="unit-vp-enforcement-normal")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Research cloud costs and summarize key points."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "research-specialist",
                    "description": "Gather cloud pricing details",
                    "prompt": "Collect 2026 prices from official docs.",
                },
            },
            "tool-3",
            {},
        )
    )
    assert result == {}


def test_vp_worker_lane_does_not_require_nested_vp_dispatch():
    hooks = AgentHookSet(
        run_id="unit-vp-worker-lane-bypass",
        active_workspace=(
            "/tmp/AGENT_RUN_WORKSPACES/"
            "vp_general_primary_external/vp-mission-1234567890abcdef"
        ),
    )
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Use the VP general to create a story and email it."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/tmp/foo.md"},
            },
            "tool-read-allowed-in-vp-worker-lane",
            {},
        )
    )
    assert result == {}
