"""Integration guard for ProcessTurnAdapter.execute()'s liveness-kill path.

Drives the REAL execute() async generator (stubbed client + process_turn, temp
DBs) to two outcomes:

1. A turn that goes idle (no events, no tool in flight) past the idle threshold
   is killed and a structured ERROR event ("Execution killed: ...") IS yielded.
   This guards the subtle ``except BaseException`` in the kill block: cancelling
   engine_task makes ``await engine_task`` re-raise asyncio.CancelledError (a
   BaseException, not Exception); a bare ``except Exception`` would let it escape
   and the ERROR event would never be emitted (the now-primary kill path).
2. A turn that keeps emitting events past the old tier wall-clock cap is NOT
   killed — it runs to completion. (The core operator requirement.)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("claude_agent_sdk", reason="SDK not installed")

from universal_agent.agent_core import AgentEvent, EventType  # noqa: E402
from universal_agent.execution_engine import (  # noqa: E402
    EngineConfig,
    ProcessTurnAdapter,
)


@pytest.fixture
def _temp_dbs(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "activity.db"))
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime.db"))
    # PRE-WARM the runtime conn exactly like a long-running gateway: in
    # production run_engine reuses main.runtime_db_conn, so its per-turn setup is
    # sub-millisecond. A COLD setup (connect + ensure_schema on a fresh DB) runs
    # SYNCHRONOUSLY and would block the event loop ~1-2s on the first turn, which
    # the watchdog (correctly) counts as idle — a cold-start artifact, not the
    # steady-state behavior under test. Warming the conn here makes the test
    # measure the steady state.
    from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
    from universal_agent.durable.migrations import ensure_schema
    import universal_agent.main as main_module

    conn = connect_runtime_db(get_runtime_db_path())
    ensure_schema(conn)
    monkeypatch.setattr(main_module, "runtime_db_conn", conn, raising=False)
    return tmp_path


def _make_adapter(tmp_path):
    cfg = EngineConfig(workspace_dir=str(tmp_path), user_id="test")
    cfg.__dict__["_run_source"] = "heartbeat"
    adapter = ProcessTurnAdapter(cfg)
    adapter._initialized = True  # skip setup_session / SDK handshake
    adapter._options = SimpleNamespace(model="glm-5.1")

    async def _fake_ensure_client():
        return object()

    adapter._ensure_client = _fake_ensure_client  # type: ignore[assignment]
    return adapter


async def _drive(adapter, *, stop_on_error=True, max_events=200):
    events: list[AgentEvent] = []
    async for ev in adapter.execute("go"):
        events.append(ev)
        if stop_on_error and ev.type == EventType.ERROR:
            break
        if len(events) >= max_events:
            break
    return events


def test_idle_turn_is_killed_and_yields_error_event(_temp_dbs, monkeypatch):
    monkeypatch.setenv("UA_PROCESS_TURN_IDLE_KILL_SECONDS", "0.3")
    monkeypatch.setenv("UA_PROCESS_TURN_ABSOLUTE_BACKSTOP_SECONDS", "3600")
    monkeypatch.delenv("UA_PROCESS_TURN_TIMEOUT_SECONDS", raising=False)
    adapter = _make_adapter(_temp_dbs)

    async def _fake_process_turn(*, event_callback=None, **_kw):
        # One sign of life, then go silent (no tool in flight) → idle kill.
        if event_callback:
            event_callback(AgentEvent(type=EventType.TEXT, data={"text": "starting"}))
        await asyncio.sleep(30)  # hang; the watchdog must reap this

    import universal_agent.main as main_module

    monkeypatch.setattr(main_module, "process_turn", _fake_process_turn, raising=False)

    events = asyncio.run(asyncio.wait_for(_drive(adapter), timeout=10))
    errors = [e for e in events if e.type == EventType.ERROR]
    assert errors, "idle turn must yield a structured ERROR event (not escape as CancelledError)"
    assert "killed" in str(errors[-1].data.get("message", "")).lower()


def test_actively_progressing_turn_past_old_cap_is_not_killed(_temp_dbs, monkeypatch):
    # Idle window 1s; the turn emits an event every ~0.2s for ~2s (well past the
    # window each time) then completes. It must NOT be killed.
    monkeypatch.setenv("UA_PROCESS_TURN_IDLE_KILL_SECONDS", "1")
    monkeypatch.setenv("UA_PROCESS_TURN_ABSOLUTE_BACKSTOP_SECONDS", "3600")
    monkeypatch.delenv("UA_PROCESS_TURN_TIMEOUT_SECONDS", raising=False)
    adapter = _make_adapter(_temp_dbs)

    async def _fake_process_turn(*, event_callback=None, **_kw):
        for i in range(10):
            await asyncio.sleep(0.2)
            if event_callback:
                event_callback(AgentEvent(type=EventType.TEXT, data={"text": f"step{i}"}))

    import universal_agent.main as main_module

    monkeypatch.setattr(main_module, "process_turn", _fake_process_turn, raising=False)

    events = asyncio.run(asyncio.wait_for(_drive(adapter, stop_on_error=False), timeout=15))
    errors = [e for e in events if e.type == EventType.ERROR]
    assert not errors, f"an actively-progressing turn must not be killed, got {errors}"
    texts = [e for e in events if e.type == EventType.TEXT]
    assert len(texts) >= 5, "expected the progressing turn's events to stream through"


def test_tool_in_flight_turn_past_idle_window_is_not_killed(_temp_dbs, monkeypatch):
    # A single tool runs silently for ~1.5s — longer than the 0.5s idle window —
    # but a tool is in flight, so the idle kill is suspended and the turn finishes.
    monkeypatch.setenv("UA_PROCESS_TURN_IDLE_KILL_SECONDS", "0.5")
    monkeypatch.setenv("UA_PROCESS_TURN_ABSOLUTE_BACKSTOP_SECONDS", "3600")
    monkeypatch.delenv("UA_PROCESS_TURN_TIMEOUT_SECONDS", raising=False)
    adapter = _make_adapter(_temp_dbs)

    async def _fake_process_turn(*, event_callback=None, **_kw):
        if event_callback:
            event_callback(AgentEvent(type=EventType.TOOL_CALL, data={"name": "Bash", "id": "t1"}))
        await asyncio.sleep(1.5)  # long silent "build" — exempt while tool in flight
        if event_callback:
            event_callback(AgentEvent(type=EventType.TOOL_RESULT, data={"id": "t1"}))
            event_callback(AgentEvent(type=EventType.TEXT, data={"text": "done"}))

    import universal_agent.main as main_module

    monkeypatch.setattr(main_module, "process_turn", _fake_process_turn, raising=False)

    events = asyncio.run(asyncio.wait_for(_drive(adapter, stop_on_error=False), timeout=15))
    errors = [e for e in events if e.type == EventType.ERROR]
    assert not errors, f"tool-in-flight time must be exempt from the idle kill, got {errors}"
