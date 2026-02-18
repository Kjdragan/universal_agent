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
