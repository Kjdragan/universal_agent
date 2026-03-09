from __future__ import annotations

from universal_agent import hooks
from universal_agent.agent_core import EventType


def test_emit_tool_call_event_includes_agent_identity():
    captured = []
    hooks.set_event_callback(captured.append)
    hooks.reset_tool_event_tracking()
    try:
        emitted = hooks.emit_tool_call_event(
            tool_use_id="tool_1",
            tool_name="Read",
            tool_input={"path": "README.md"},
            input_data={
                "agent_id": "agent_abc",
                "agent_type": "research-specialist",
                "parent_tool_use_id": "parent_1",
            },
        )
        assert emitted is True
        assert captured
        event = captured[-1]
        assert event.type == EventType.TOOL_CALL
        assert event.data["agent_id"] == "agent_abc"
        assert event.data["agent_type"] == "research-specialist"
        assert event.data["parent_tool_use_id"] == "parent_1"
    finally:
        hooks.set_event_callback(None)
        hooks.reset_tool_event_tracking()


def test_emit_tool_result_event_falls_back_to_subagent_type():
    captured = []
    hooks.set_event_callback(captured.append)
    hooks.reset_tool_event_tracking()
    try:
        emitted = hooks.emit_tool_result_event(
            tool_use_id="tool_2",
            is_error=False,
            tool_result={"content": "ok"},
            input_data={
                "subagent_type": "report-writer",
            },
        )
        assert emitted is True
        event = captured[-1]
        assert event.type == EventType.TOOL_RESULT
        assert event.data["agent_type"] == "report-writer"
        assert event.data["tool_use_id"] == "tool_2"
    finally:
        hooks.set_event_callback(None)
        hooks.reset_tool_event_tracking()
