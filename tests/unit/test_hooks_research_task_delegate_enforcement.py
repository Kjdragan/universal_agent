import asyncio

from universal_agent.hooks import AgentHookSet


def _run(coro):
    return asyncio.run(coro)


def test_allows_agent_tool_for_research_delegate_first_flow():
    hooks = AgentHookSet(run_id="unit-research-agent-allow")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {
                "prompt": (
                    "Search for the latest information, create a report, save as pdf, and email it."
                )
            }
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "research-specialist",
                    "description": "research",
                    "prompt": "collect sources",
                },
            },
            "tool-agent-research",
            {},
        )
    )
    assert result == {}


def test_allows_task_tool_for_research_delegate_first_flow():
    hooks = AgentHookSet(run_id="unit-research-task-allow")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {
                "prompt": (
                    "Search for the latest information, create a report, save as pdf, and email it."
                )
            }
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "research-specialist",
                    "description": "research",
                    "prompt": "collect sources",
                },
            },
            "tool-task-research",
            {},
        )
    )
    assert result == {}


def test_allows_todowrite_before_research_delegate_first_flow():
    hooks = AgentHookSet(run_id="unit-research-todowrite-allow")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {
                "prompt": (
                    "Research this topic, create a report, save as PDF, and email it."
                )
            }
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "TodoWrite",
                "tool_input": {
                    "todos": [
                        {
                            "content": "Research topic",
                            "status": "in_progress",
                            "activeForm": "Researching topic",
                        }
                    ]
                },
            },
            "tool-todowrite",
            {},
        )
    )
    assert result == {}
