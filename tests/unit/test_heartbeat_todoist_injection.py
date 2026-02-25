from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_heartbeat_injects_todoist_summary_only_when_actionable(monkeypatch, tmp_path):
    import universal_agent.heartbeat_service as hb
    from universal_agent.gateway import GatewaySession

    class DummyGateway:
        async def execute(self, session, request):
            if False:
                yield None

    class DummyCM:
        def __init__(self):
            self.session_connections = {}

        async def broadcast(self, session_id, payload):
            return None

    captured: dict[str, object] = {}

    @dataclass
    class CapturingGatewayRequest:
        user_input: str
        force_complex: bool = False
        metadata: dict = None  # type: ignore[assignment]

        def __post_init__(self):
            captured["metadata"] = self.metadata

    monkeypatch.setattr(hb, "GatewayRequest", CapturingGatewayRequest)
    monkeypatch.setenv("UA_HEARTBEAT_MOCK_RESPONSE", "0")
    monkeypatch.setenv("UA_HEARTBEAT_OK_TOKENS", "UA_HEARTBEAT_OK")

    service = hb.HeartbeatService(DummyGateway(), DummyCM())
    # Keep one baseline system event so this test exercises metadata injection,
    # not the no-actionable fast-path skip.
    service.system_event_provider = lambda _sid: [{"type": "seed", "payload": {}}]

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "HEARTBEAT.md").write_text("UA_HEARTBEAT_OK", encoding="utf-8")

    session = GatewaySession(session_id="s1", user_id="u", workspace_dir=str(workspace), metadata={})
    state = hb.HeartbeatState()
    state_path = Path(workspace) / hb.HEARTBEAT_STATE_FILE
    schedule = service.default_schedule
    delivery = service.default_delivery
    visibility = service.default_visibility

    class FakeTodoService:
        def __init__(self, actionable: int, candidates: list[dict] | None = None):
            self._actionable = actionable
            self._candidates = list(candidates or [])

        def heartbeat_summary(self):
            return {"timestamp": "now", "actionable_count": self._actionable, "tasks": []}

        def heartbeat_brainstorm_candidates(self, limit: int = 3):
            return self._candidates[:limit]

    # Case A: actionable=0 => no todoist_summary
    monkeypatch.setattr(
        "universal_agent.services.todoist_service.TodoService",
        lambda: FakeTodoService(0),
    )
    await service._run_heartbeat(
        session,
        state,
        state_path,
        "UA_HEARTBEAT_OK",
        schedule,
        delivery,
        visibility,
    )
    md = captured.get("metadata")
    assert isinstance(md, dict)
    assert "todoist_summary" not in md

    # Case B: actionable>0 => inject todoist_summary
    captured.clear()
    monkeypatch.setattr(
        "universal_agent.services.todoist_service.TodoService",
        lambda: FakeTodoService(2),
    )
    await service._run_heartbeat(
        session,
        state,
        state_path,
        "UA_HEARTBEAT_OK",
        schedule,
        delivery,
        visibility,
    )
    md2 = captured.get("metadata")
    assert isinstance(md2, dict)
    assert md2.get("todoist_summary", {}).get("actionable_count") == 2

    # Case C: actionable=0 with brainstorm candidates => inject candidate metadata
    captured.clear()
    monkeypatch.setattr(
        "universal_agent.services.todoist_service.TodoService",
        lambda: FakeTodoService(
            0,
            candidates=[
                {
                    "id": "idea_1",
                    "content": "Investigate retry policy",
                    "section": "heartbeat_candidate",
                    "confidence": 2,
                }
            ],
        ),
    )
    await service._run_heartbeat(
        session,
        state,
        state_path,
        "UA_HEARTBEAT_OK",
        schedule,
        delivery,
        visibility,
    )
    md3 = captured.get("metadata")
    assert isinstance(md3, dict)
    candidates = md3.get("todoist_brainstorm_candidates")
    assert isinstance(candidates, list)
    assert candidates and candidates[0].get("id") == "idea_1"


@pytest.mark.asyncio
async def test_heartbeat_skips_agent_run_when_todoist_has_no_actionable(monkeypatch, tmp_path):
    import universal_agent.heartbeat_service as hb
    from universal_agent.gateway import GatewaySession

    class DummyGateway:
        def __init__(self):
            self.execute_calls = 0

        async def execute(self, session, request):
            self.execute_calls += 1
            if False:
                yield None

    class DummyCM:
        def __init__(self):
            self.session_connections = {}

        async def broadcast(self, session_id, payload):
            return None

    monkeypatch.setenv("UA_HEARTBEAT_MOCK_RESPONSE", "0")
    monkeypatch.setenv("UA_HEARTBEAT_OK_TOKENS", "UA_HEARTBEAT_OK")

    class FakeTodoService:
        def heartbeat_summary(self):
            return {"timestamp": "now", "actionable_count": 0, "tasks": []}

    monkeypatch.setattr(
        "universal_agent.services.todoist_service.TodoService",
        lambda: FakeTodoService(),
    )

    gateway = DummyGateway()
    service = hb.HeartbeatService(gateway, DummyCM())
    service.system_event_provider = lambda _sid: []

    workspace = tmp_path / "ws2"
    workspace.mkdir()
    (workspace / "HEARTBEAT.md").write_text("UA_HEARTBEAT_OK", encoding="utf-8")

    session = GatewaySession(session_id="s2", user_id="u", workspace_dir=str(workspace), metadata={})
    state = hb.HeartbeatState()
    state_path = Path(workspace) / hb.HEARTBEAT_STATE_FILE

    await service._run_heartbeat(
        session,
        state,
        state_path,
        "UA_HEARTBEAT_OK",
        service.default_schedule,
        service.default_delivery,
        service.default_visibility,
    )

    assert gateway.execute_calls == 0


@pytest.mark.asyncio
async def test_heartbeat_does_not_skip_when_only_brainstorm_candidates_exist(monkeypatch, tmp_path):
    import universal_agent.heartbeat_service as hb
    from universal_agent.gateway import GatewaySession

    class DummyGateway:
        def __init__(self):
            self.execute_calls = 0

        async def execute(self, session, request):
            self.execute_calls += 1
            if False:
                yield None

    class DummyCM:
        def __init__(self):
            self.session_connections = {}

        async def broadcast(self, session_id, payload):
            return None

    monkeypatch.setenv("UA_HEARTBEAT_MOCK_RESPONSE", "0")
    monkeypatch.setenv("UA_HEARTBEAT_OK_TOKENS", "UA_HEARTBEAT_OK")

    class FakeTodoService:
        def heartbeat_summary(self):
            return {"timestamp": "now", "actionable_count": 0, "tasks": []}

        def heartbeat_brainstorm_candidates(self, limit: int = 3):
            return [{"id": "idea_2", "content": "Prototype fallback policy"}]

    monkeypatch.setattr(
        "universal_agent.services.todoist_service.TodoService",
        lambda: FakeTodoService(),
    )

    gateway = DummyGateway()
    service = hb.HeartbeatService(gateway, DummyCM())
    service.system_event_provider = lambda _sid: []

    workspace = tmp_path / "ws3"
    workspace.mkdir()
    (workspace / "HEARTBEAT.md").write_text("UA_HEARTBEAT_OK", encoding="utf-8")

    session = GatewaySession(session_id="s3", user_id="u", workspace_dir=str(workspace), metadata={})
    state = hb.HeartbeatState()
    state_path = Path(workspace) / hb.HEARTBEAT_STATE_FILE

    await service._run_heartbeat(
        session,
        state,
        state_path,
        "UA_HEARTBEAT_OK",
        service.default_schedule,
        service.default_delivery,
        service.default_visibility,
    )

    assert gateway.execute_calls == 1


def test_heartbeat_guard_policy_enforces_actionable_capacity(monkeypatch):
    import universal_agent.heartbeat_service as hb

    monkeypatch.setenv("UA_HEARTBEAT_MAX_ACTIONABLE", "1")
    policy = hb._heartbeat_guard_policy(
        actionable_count=3,
        brainstorm_candidate_count=0,
        system_event_count=0,
        has_exec_completion=False,
    )
    assert policy["skip_reason"] == "actionable_over_capacity"


def test_heartbeat_guard_policy_can_disable_proactive_runs(monkeypatch):
    import universal_agent.heartbeat_service as hb

    monkeypatch.setenv("UA_HEARTBEAT_AUTONOMOUS_ENABLED", "0")
    policy = hb._heartbeat_guard_policy(
        actionable_count=1,
        brainstorm_candidate_count=0,
        system_event_count=0,
        has_exec_completion=False,
    )
    assert policy["skip_reason"] == "autonomous_disabled"
