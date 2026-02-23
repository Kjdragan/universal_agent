from universal_agent.mission_guardrails import build_mission_contract, MissionGuardrailTracker


def test_build_contract_detects_interim_and_final_email_requirements():
    prompt = (
        "Email me each interim work product and Gmail me the final work product once complete."
    )
    contract = build_mission_contract(prompt)
    assert contract.email_required is True
    assert contract.interim_required is True
    assert contract.final_required is True
    assert contract.min_email_sends == 2


def test_goal_satisfaction_fails_when_required_email_sends_missing():
    contract = build_mission_contract("Use Gmail for interim updates and final output")
    tracker = MissionGuardrailTracker(contract)
    tracker.record_tool_call("mcp__composio__GMAIL_LIST_THREADS")

    result = tracker.evaluate()
    assert result["passed"] is False
    assert result["missing"]
    assert result["observed"]["email_send_count"] == 0


def test_goal_satisfaction_passes_when_required_email_sends_recorded():
    contract = build_mission_contract("Use Gmail for interim updates and final output")
    tracker = MissionGuardrailTracker(contract)
    tracker.record_tool_call("mcp__composio__GMAIL_SEND_EMAIL")
    tracker.record_tool_call("mcp__composio__GMAIL_SEND_EMAIL")

    result = tracker.evaluate()
    assert result["passed"] is True
    assert result["observed"]["email_send_count"] == 2
    assert result["missing"] == []


def test_goal_satisfaction_counts_nested_multi_execute_gmail_send():
    contract = build_mission_contract("Use Gmail for final output")
    tracker = MissionGuardrailTracker(contract)
    tracker.record_tool_call(
        "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
        tool_input={
            "tools": [
                {
                    "tool_slug": "GMAIL_SEND_EMAIL",
                    "arguments": {"recipient_email": "me", "subject": "x", "body": "y"},
                }
            ]
        },
    )

    result = tracker.evaluate()
    assert result["passed"] is True
    assert result["observed"]["email_send_count"] == 1
    assert result["observed"]["gmail_send_count"] == 1


def test_research_pipeline_adherence_passes_when_run_phase_called_before_scouting():
    contract = build_mission_contract("Research latest updates and send report")
    tracker = MissionGuardrailTracker(contract)
    tracker.record_tool_call(
        "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
        tool_input={
            "tools": [
                {"tool_slug": "COMPOSIO_SEARCH_NEWS", "arguments": {"query": "x"}},
            ]
        },
    )
    tracker.record_tool_call("mcp__internal__run_research_phase")
    tracker.record_tool_call("Bash")

    result = tracker.evaluate()
    adherence = result["observed"]["research_pipeline_adherence"]
    assert adherence["required"] is True
    assert adherence["run_research_phase_called"] is True
    assert adherence["pre_phase_workspace_scouting_calls"] == 0
    assert adherence["passed"] is True


def test_research_pipeline_adherence_fails_when_scouting_happens_before_run_phase():
    contract = build_mission_contract("Research latest updates and send report")
    tracker = MissionGuardrailTracker(contract)
    tracker.record_tool_call(
        "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
        tool_input={
            "tools": [
                {"tool_slug": "COMPOSIO_SEARCH_WEB", "arguments": {"query": "x"}},
            ]
        },
    )
    tracker.record_tool_call("mcp__internal__list_directory")

    result = tracker.evaluate()
    adherence = result["observed"]["research_pipeline_adherence"]
    assert adherence["required"] is True
    assert adherence["run_research_phase_called"] is False
    assert adherence["pre_phase_workspace_scouting_calls"] == 1
    assert adherence["passed"] is False
