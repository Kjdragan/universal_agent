"""VP SDK-path no-progress (idle) kill — the shared event-consumption helper.

Guards that a hung SDK mission (no events) is reaped after the idle threshold,
while a mission that keeps emitting events runs to completion. Idle-based, not a
wall-clock cap.
"""
from __future__ import annotations

import asyncio

from universal_agent.agent_core import EventType
from universal_agent.vp.clients.base import consume_adapter_events_with_idle_timeout


class _Ev:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


class _FakeAdapter:
    """Minimal adapter stub. Yields the given events, then optionally hangs
    forever (to simulate a stalled mission)."""

    def __init__(self, events, hang=False):
        self._events = events
        self._hang = hang
        self.closed = False

    async def execute(self, prompt):  # noqa: ARG002 — prompt unused in stub
        for ev in self._events:
            yield ev
        if self._hang:
            await asyncio.Event().wait()  # never completes


def test_extracts_final_text_and_trace():
    events = [
        _Ev(EventType.TEXT, {"final": True, "text": "all done"}),
        _Ev(EventType.ITERATION_END, {"trace_id": "trace-123"}),
    ]
    adapter = _FakeAdapter(events)
    final_text, error_text, trace_id = asyncio.run(
        consume_adapter_events_with_idle_timeout(adapter, "go", idle_timeout_seconds=5)
    )
    assert final_text == "all done"
    assert error_text is None
    assert trace_id == "trace-123"


def test_error_event_surfaces():
    events = [_Ev(EventType.ERROR, {"message": "boom"})]
    adapter = _FakeAdapter(events)
    final_text, error_text, trace_id = asyncio.run(
        consume_adapter_events_with_idle_timeout(adapter, "go", idle_timeout_seconds=5)
    )
    assert error_text == "boom"


def test_idle_hang_is_killed():
    adapter = _FakeAdapter([], hang=True)
    final_text, error_text, trace_id = asyncio.run(
        consume_adapter_events_with_idle_timeout(
            adapter, "go", idle_timeout_seconds=0.05
        )
    )
    assert error_text is not None
    assert "no_progress_timeout" in error_text


def test_idle_timeout_disabled_consumes_normally():
    events = [_Ev(EventType.TEXT, {"final": True, "text": "ok"})]
    adapter = _FakeAdapter(events)
    final_text, error_text, trace_id = asyncio.run(
        consume_adapter_events_with_idle_timeout(adapter, "go", idle_timeout_seconds=0)
    )
    assert final_text == "ok"
    assert error_text is None


def test_progress_resets_idle_window():
    async def _scenario():
        class _SlowAdapter:
            async def execute(self, prompt):  # noqa: ARG002
                for i in range(3):
                    await asyncio.sleep(0.03)  # < idle window each time
                    yield _Ev(EventType.TEXT, {"final": i == 2, "text": f"step{i}"})

        return await consume_adapter_events_with_idle_timeout(
            _SlowAdapter(), "go", idle_timeout_seconds=0.2
        )

    final_text, error_text, trace_id = asyncio.run(_scenario())
    assert final_text == "step2"
    assert error_text is None


def test_tool_in_flight_exempts_idle_kill():
    """A long-running tool (build/test) emits TOOL_CALL then is silent for longer
    than the idle window before its TOOL_RESULT — it must NOT be killed mid-tool.
    Without the tool-in-flight exemption the silent gap would trip the idle kill
    and the final text would be lost."""

    async def _scenario():
        class _BuildAdapter:
            async def execute(self, prompt):  # noqa: ARG002
                yield _Ev(EventType.TOOL_CALL, {"name": "Bash"})
                # Silent build well past the idle window — exempt because a tool
                # is in flight.
                await asyncio.sleep(0.25)
                yield _Ev(EventType.TOOL_RESULT, {"ok": True})
                yield _Ev(EventType.TEXT, {"final": True, "text": "built"})

        return await consume_adapter_events_with_idle_timeout(
            _BuildAdapter(), "go", idle_timeout_seconds=0.1
        )

    final_text, error_text, trace_id = asyncio.run(_scenario())
    assert final_text == "built", "tool-in-flight time must be exempt from idle kill"
    assert error_text is None


def test_idle_kill_arms_after_tool_completes():
    """Once a tool returns, the idle window re-arms: if inference then stalls
    with no tool in flight, the run is reaped."""

    async def _scenario():
        class _StallAfterToolAdapter:
            async def execute(self, prompt):  # noqa: ARG002
                yield _Ev(EventType.TOOL_CALL, {"name": "Bash"})
                yield _Ev(EventType.TOOL_RESULT, {"ok": True})
                # No tool in flight now; go silent past the idle window.
                await asyncio.Event().wait()  # never completes

        return await consume_adapter_events_with_idle_timeout(
            _StallAfterToolAdapter(), "go", idle_timeout_seconds=0.05
        )

    final_text, error_text, trace_id = asyncio.run(_scenario())
    assert error_text is not None
    assert "no_progress_timeout" in error_text
