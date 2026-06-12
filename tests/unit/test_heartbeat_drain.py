"""Behavior tests for graceful heartbeat drain on shutdown (deploy-restart C2).

These exercise the real ``HeartbeatService.start()``/``stop()`` scheduler loop
with a controllable in-flight iteration (``_process_session`` replaced by a
test double) so we verify *observable behavior* — does an in-flight iteration
complete or get cancelled on shutdown — not internal structure.

Harm under test: **H2** — a gateway restart SIGTERMs an in-flight Simone
heartbeat iteration. The drain lets it finish within a bounded budget; on
timeout it falls back to today's cancel. See
``project_docs/06_platform/12_deploy_restart_resilience_adr.md`` §C2.
"""

import asyncio
import time

from universal_agent.heartbeat_service import HeartbeatService


class _StubSession:
    def __init__(self, session_id: str):
        self.session_id = session_id


class _ControllableIteration:
    """A fake heartbeat iteration whose timing the test controls via events."""

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.completed = False
        self.cancelled = False

    async def __call__(self, session):
        self.started.set()
        try:
            await self.release.wait()
            self.completed = True
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _make_service() -> HeartbeatService:
    svc = HeartbeatService(gateway=object(), connection_manager=object())
    # Don't emit activity-DB tick rows in a unit test.
    svc._tick_emit_interval_s = 1e9
    svc._last_tick_emit_at = time.time()
    return svc


async def _start_with_iteration(svc, it):
    svc._process_session = it  # test double for one heartbeat iteration
    svc.active_sessions = {"s1": _StubSession("s1")}
    await svc.start()
    await asyncio.wait_for(it.started.wait(), timeout=2.0)


async def test_drain_lets_inflight_iteration_complete():
    svc = _make_service()
    it = _ControllableIteration()
    await _start_with_iteration(svc, it)

    # Release the iteration shortly after the drain begins (within budget).
    asyncio.get_event_loop().call_later(0.05, it.release.set)
    await svc.stop(drain=True, drain_timeout=2.0)

    assert it.completed is True
    assert it.cancelled is False
    assert svc.running is False


async def test_drain_budget_exceeded_falls_back_to_cancel():
    svc = _make_service()
    it = _ControllableIteration()
    await _start_with_iteration(svc, it)

    # Never release -> the iteration outlives the drain budget -> cancel fallback.
    await svc.stop(drain=True, drain_timeout=0.1)

    assert it.cancelled is True
    assert it.completed is False
    assert svc.running is False


async def test_drain_is_fast_when_no_iteration_in_flight():
    """An idle restart must not wait the full budget — there is nothing to drain."""
    svc = _make_service()
    svc.active_sessions = {}  # nothing to process -> loop just sleeps
    await svc.start()
    await asyncio.sleep(0.05)  # let the loop reach its sleep

    t0 = time.monotonic()
    await svc.stop(drain=True, drain_timeout=5.0)
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0  # did NOT wait the 5s budget
    assert svc.running is False


async def test_default_stop_cancels_inflight_immediately():
    """Backward compatibility: stop() with no drain preserves the legacy
    immediate-cancel behavior."""
    svc = _make_service()
    it = _ControllableIteration()
    await _start_with_iteration(svc, it)

    await svc.stop()  # drain defaults to False

    assert it.cancelled is True
    assert it.completed is False


async def test_stop_when_not_running_is_noop():
    svc = _make_service()
    # Never started; should not raise.
    await svc.stop(drain=True)
    assert svc.running is False
