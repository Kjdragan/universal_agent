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


def test_todo_execution_vp_dispatch_without_manual_delegate_requests_auto_delegate():
    tracker = MissionGuardrailTracker(
        build_mission_contract("Create a comprehensive report and email it to me."),
        run_kind="todo_execution",
    )
    tracker.record_tool_call(
        "mcp__internal__vp_dispatch_mission",
        tool_input={"vp_id": "vp.general.primary", "objective": "Handle task email:1"},
    )
    tracker.record_tool_result(
        "mcp__internal__vp_dispatch_mission",
        tool_input={"vp_id": "vp.general.primary", "objective": "Handle task email:1"},
        tool_result='{"ok": true, "mission_id": "mission-123", "vp_id": "vp.general.primary"}',
    )

    result = tracker.evaluate()

    assert result["passed"] is True
    assert result["stage_status"] == "auto_delegate"
    assert result["terminal"] is False
    assert result["observed"]["successful_vp_dispatches"][0]["mission_id"] == "mission-123"


def test_todo_execution_without_lifecycle_mutation_fails():
    tracker = MissionGuardrailTracker(
        build_mission_contract("Create a comprehensive report and email it to me."),
        run_kind="todo_execution",
    )

    result = tracker.evaluate()

    assert result["passed"] is False
    assert result["missing"][0]["requirement"] == "lifecycle_mutation"


def test_code_gen_request_does_not_trigger_email_required():
    """Regression test: a pure code-generation request should not require email."""
    raw_user_input = "Direct Codie to create a Gemini Interactions API demo application at /tmp/test"
    contract = build_mission_contract(raw_user_input)

    assert contract.email_required is False
    assert contract.min_email_sends == 0


def test_inflated_execution_prompt_would_falsely_trigger_email():
    """Documents that the inflated prompt template contains email-triggering
    language.  If build_mission_contract ever receives this instead of the
    raw user input, email_required would be a false positive."""
    # This is the actual delivery contract text appended by build_todo_execution_prompt
    # for interactive_chat tasks.  The phrase "do not send email unless" contains
    # the token pair "send ... email" which matches _EMAIL_ACTION_PATTERNS[2].
    inflated = (
        "You are Simone. Execute the assigned work items.\n"
        "== DELIVERY CONTRACT ==\n"
        "For interactive_chat: deliver the final answer in this chat session "
        "and do not send email unless the user explicitly asked for email delivery.\n"
        "Work Item 1: Direct Codie to create a demo application at /tmp/test\n"
    )
    contract = build_mission_contract(inflated)
    # The inflated prompt matches _EMAIL_ACTION_PATTERNS ("send ... email"),
    # which is exactly the false positive we guard against.
    assert contract.email_required is True, (
        "If this starts passing as False, the prompt template language changed "
        "and the guard in todo_dispatch_service.py may no longer be necessary."
    )

