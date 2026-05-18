"""Regression: vp_dispatch_mission tool call should satisfy the email
contract in a todo_execution turn, even when the dispatch response payload
omits ``mission_id`` or doesn't expose ``ok=True``.

2026-05-18 05:09 + 13:26 incidents: Simone executed two todo_execution
turns whose tool sequence was
``mcp__internal__vp_dispatch_mission`` -> ``mcp__internal__task_hub_task_action(complete)``.
The mission contract required ``email_send_count >= 1``; Simone sent
zero emails herself because the VP fired its own ``[VP Status]`` email
back to Kevin. ``successful_vp_dispatches`` was empty in both
checkpoints — the result tracker requires a specific payload shape
and the dispatched response didn't expose it. The contract therefore
fell to the ``Final completion attempted without the required email
delivery step`` branch and emitted ``[ERROR] Mission Guardrail Blocked
Completion`` to Kevin.

This fix treats *any* recorded ``vp_dispatch_mission`` tool call as
evidence of delegation, falling back from the stricter
``successful_vp_dispatches`` list when the payload shape regresses.
"""

from __future__ import annotations

import pytest

from universal_agent.mission_guardrails import (
    MissionContract,
    MissionGuardrailTracker,
)


def _contract(*, email_required: bool = True) -> MissionContract:
    return MissionContract(
        email_required=email_required,
        min_email_sends=1 if email_required else 0,
        interim_required=False,
        final_required=email_required,
    )


def _make_tracker(*, email_required: bool = True) -> MissionGuardrailTracker:
    return MissionGuardrailTracker(_contract(email_required=email_required), run_kind="todo_execution")


# ── _vp_dispatch_attempted property ─────────────────────────────────────────


def test_vp_dispatch_attempted_false_by_default() -> None:
    tracker = _make_tracker()
    assert tracker._vp_dispatch_attempted is False


def test_vp_dispatch_attempted_true_after_tool_call() -> None:
    tracker = _make_tracker()
    tracker.record_tool_call("mcp__internal__vp_dispatch_mission")
    assert tracker._vp_dispatch_attempted is True


def test_vp_dispatch_attempted_matches_case_insensitively() -> None:
    tracker = _make_tracker()
    tracker.record_tool_call("MCP__Internal__VP_Dispatch_Mission")
    assert tracker._vp_dispatch_attempted is True


def test_vp_dispatch_attempted_ignores_other_tools() -> None:
    tracker = _make_tracker()
    tracker.record_tool_call("mcp__internal__task_hub_task_action")
    tracker.record_tool_call("Bash")
    assert tracker._vp_dispatch_attempted is False


# ── contract evaluation ─────────────────────────────────────────────────────


def test_email_required_complete_after_vp_dispatch_passes() -> None:
    """Regression for 2026-05-18 05:09/13:26 incidents.

    Simone called vp_dispatch_mission + task_hub_task_action(complete)
    with zero email sends. Contract requires email but VP delegation
    should mark the turn auto_delegate (non-terminal) instead of failing.
    """
    tracker = _make_tracker(email_required=True)
    tracker.record_tool_call("mcp__internal__vp_dispatch_mission")
    # Result payload missing both mission_id and explicit ok=True —
    # exactly the regression shape from production.
    tracker.record_tool_result(
        "mcp__internal__vp_dispatch_mission",
        tool_input={"vp_id": "vp.coder.primary", "objective": "..."},
        tool_result={"status": "queued"},
    )
    tracker.record_tool_call(
        "mcp__internal__task_hub_task_action",
        tool_input={"action": "complete", "task_id": "task:abc"},
    )

    result = tracker.evaluate()
    assert result["passed"] is True, result
    assert result["stage_status"] == "auto_delegate"
    assert result["terminal"] is False
    assert result["missing"] == []


def test_successful_vp_dispatch_with_mission_id_still_works() -> None:
    """The richer successful_vp_dispatches path must still work when the
    payload IS well-formed."""
    tracker = _make_tracker(email_required=True)
    tracker.record_tool_call("mcp__internal__vp_dispatch_mission")
    tracker.record_tool_result(
        "mcp__internal__vp_dispatch_mission",
        tool_input={"vp_id": "vp.coder.primary"},
        tool_result={"mission_id": "vp-mission-abc", "ok": True},
    )
    tracker.record_tool_call(
        "mcp__internal__task_hub_task_action",
        tool_input={"action": "complete", "task_id": "task:abc"},
    )

    result = tracker.evaluate()
    assert result["passed"] is True
    assert result["stage_status"] == "auto_delegate"


def test_email_required_complete_without_any_dispatch_still_fails() -> None:
    """If Simone never tried vp_dispatch_mission, no email = real failure."""
    tracker = _make_tracker(email_required=True)
    tracker.record_tool_call(
        "mcp__internal__task_hub_task_action",
        tool_input={"action": "complete", "task_id": "task:abc"},
    )

    result = tracker.evaluate()
    assert result["passed"] is False
    missing_requirements = [m.get("requirement") for m in result["missing"]]
    assert "email_send" in missing_requirements


def test_vp_dispatch_alone_marks_auto_delegate_non_terminal() -> None:
    """vp_dispatch_mission alone (no lifecycle action) routes to auto_delegate.

    Matches the existing design intent of the successful_vp_dispatches
    path: the dispatch is the delegation signal and the VP carries the
    work to completion.
    """
    tracker = _make_tracker(email_required=True)
    tracker.record_tool_call("mcp__internal__vp_dispatch_mission")

    result = tracker.evaluate()
    assert result["passed"] is True
    assert result["stage_status"] == "auto_delegate"
    assert result["terminal"] is False


def test_non_email_branch_also_accepts_vp_dispatch_attempt() -> None:
    """Mirror check: the non-email_required branch at line 198+ uses the
    same successful_vp_dispatches gate. The fix should apply there too."""
    tracker = _make_tracker(email_required=False)
    tracker.record_tool_call("mcp__internal__vp_dispatch_mission")
    tracker.record_tool_result(
        "mcp__internal__vp_dispatch_mission",
        tool_input={"vp_id": "vp.coder.primary"},
        tool_result={"queued": True},
    )

    result = tracker.evaluate()
    assert result["passed"] is True
    assert result["stage_status"] == "auto_delegate"
    assert result["terminal"] is False
