from __future__ import annotations

from universal_agent.mission_guardrails import MissionGuardrailTracker, build_mission_contract


def test_email_triage_runs_do_not_fail_email_delivery_guardrails():
    tracker = MissionGuardrailTracker(
        build_mission_contract("Please email me a comprehensive report."),
        run_kind="email_triage",
    )

    result = tracker.evaluate()

    assert result["passed"] is True
    assert result["stage_status"] == "triaged"
    assert result["terminal"] is False


def test_todo_execution_delegate_is_non_terminal_not_failure():
    tracker = MissionGuardrailTracker(
        build_mission_contract("Create a comprehensive report and email it to me."),
        run_kind="todo_execution",
    )
    tracker.record_tool_call(
        "mcp__internal__task_hub_task_action",
        tool_input={"action": "delegate", "reason": "vp.general.primary"},
    )

    result = tracker.evaluate()

    assert result["passed"] is True
    assert result["stage_status"] == "delegated"
    assert result["terminal"] is False


def test_todo_execution_complete_without_email_still_fails():
    tracker = MissionGuardrailTracker(
        build_mission_contract("Create a comprehensive report and email it to me."),
        run_kind="todo_execution",
    )
    tracker.record_tool_call(
        "mcp__internal__task_hub_task_action",
        tool_input={"action": "complete"},
    )

    result = tracker.evaluate()

    assert result["passed"] is False
    assert result["missing"][0]["requirement"] == "email_send"
