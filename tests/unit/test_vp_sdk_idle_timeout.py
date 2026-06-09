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
