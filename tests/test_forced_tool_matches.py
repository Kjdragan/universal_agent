from universal_agent import main as agent_main
from universal_agent.durable.normalize import deterministic_task_key


def test_forced_tool_matches_task_ignores_task_key():
    tool_input = {
        "subagent_type": "report-creation-expert",
        "prompt": "Create a report",
    }
    expected_input = dict(tool_input)
    expected_input["task_key"] = deterministic_task_key(expected_input)

    expected = {
        "tool_name": "task",
        "tool_namespace": "claude_code",
        "tool_input": expected_input,
        "normalized_input": agent_main._normalize_tool_input(expected_input),
    }

    assert (
        agent_main._forced_tool_matches("Task", tool_input, expected)
        is True
    )


def test_forced_tool_matches_task_rejects_mismatch():
    tool_input = {
        "subagent_type": "report-creation-expert",
        "prompt": "Create a report",
    }
    expected_input = dict(tool_input)
    expected_input["task_key"] = deterministic_task_key(expected_input)

    expected = {
        "tool_name": "task",
        "tool_namespace": "claude_code",
        "tool_input": expected_input,
        "normalized_input": agent_main._normalize_tool_input(expected_input),
    }

    assert (
        agent_main._forced_tool_matches("Task", {"prompt": "Different"}, expected)
        is False
    )
