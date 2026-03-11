from universal_agent import main as agent_main


def test_format_tool_display_name_includes_skill_name():
    label = agent_main._format_tool_display_name(
        "Skill",
        {"skill": "agentmail", "args": "send mail"},
    )
    assert label == "Skill(agentmail)"


def test_format_tool_display_name_handles_missing_skill_name():
    label = agent_main._format_tool_display_name("Skill", {"args": "send mail"})
    assert label == "Skill"


def test_format_tool_display_name_passthrough_non_skill():
    label = agent_main._format_tool_display_name(
        "mcp__internal__run_research_phase",
        {"query": "x"},
    )
    assert label == "mcp__internal__run_research_phase"
