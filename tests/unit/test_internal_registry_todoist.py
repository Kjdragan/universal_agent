from __future__ import annotations


def test_internal_registry_includes_todoist_tools():
    from universal_agent.tools.internal_registry import get_core_internal_tools

    tools = get_core_internal_tools()
    names = [getattr(t, "name", getattr(t, "__name__", str(t))) for t in tools]

    assert "todoist_setup" in names
    assert "todoist_query" in names
    assert "todoist_get_task" in names
    assert "todoist_task_action" in names
    assert "todoist_idea_action" in names
