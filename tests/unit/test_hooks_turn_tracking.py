from __future__ import annotations

from types import SimpleNamespace

import pytest

from universal_agent.hooks_service import HookAction, HooksService


class _FakeGateway:
    def __init__(self):
        self.execute_calls = 0

    async def resume_session(self, session_id: str):
        return SimpleNamespace(session_id=session_id, workspace_dir=f"/tmp/{session_id}")

    async def execute(self, session, request):
        self.execute_calls += 1
        yield SimpleNamespace(type="tool_call", data={"name": "Write"})
        yield SimpleNamespace(type="iteration_end", data={"duration_seconds": 1.25, "tool_calls": 1})


@pytest.mark.asyncio
async def test_hook_dispatch_tracks_turn_lifecycle(monkeypatch):
    import universal_agent.hooks_service as hs

    monkeypatch.setattr(hs, "load_ops_config", lambda: {})
    gateway = _FakeGateway()

    admitted: list[tuple[str, str]] = []
    finalized: list[tuple[str, str, str, str | None, dict]] = []
    starts: list[tuple[str, str]] = []
    finishes: list[tuple[str, str]] = []

    async def admit(session_id: str, request):
        admitted.append((session_id, request.user_input))
        return {"decision": "accepted", "turn_id": "turn_hook_1"}

    async def finalize(session_id: str, turn_id: str, status: str, error_message, completion):
        finalized.append((session_id, turn_id, status, error_message, completion or {}))

    service = HooksService(
        gateway,
        turn_admitter=admit,
        turn_finalizer=finalize,
        run_counter_start=lambda sid, src: starts.append((sid, src)),
        run_counter_finish=lambda sid, src: finishes.append((sid, src)),
    )

    action = HookAction(kind="agent", session_key="yt_test_123", message="run hook")
    await service._dispatch_action(action)

    assert admitted and admitted[0][0] == "session_hook_yt_test_123"
    assert starts == [("session_hook_yt_test_123", "webhook")]
    assert finishes == [("session_hook_yt_test_123", "webhook")]
    assert gateway.execute_calls == 1
    assert finalized
    assert finalized[0][1] == "turn_hook_1"
    assert finalized[0][2] == "completed"
    assert finalized[0][4].get("tool_calls") == 1


@pytest.mark.asyncio
async def test_hook_dispatch_skips_when_turn_not_admitted(monkeypatch):
    import universal_agent.hooks_service as hs

    monkeypatch.setattr(hs, "load_ops_config", lambda: {})
    gateway = _FakeGateway()

    starts: list[tuple[str, str]] = []

    async def admit_busy(session_id: str, request):
        return {"decision": "busy", "turn_id": "turn_busy"}

    service = HooksService(
        gateway,
        turn_admitter=admit_busy,
        run_counter_start=lambda sid, src: starts.append((sid, src)),
    )

    action = HookAction(kind="agent", session_key="yt_test_busy", message="run hook")
    await service._dispatch_action(action)

    assert gateway.execute_calls == 0
    assert starts == []
