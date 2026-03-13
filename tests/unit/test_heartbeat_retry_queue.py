from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


class _DummyGateway:
    async def execute(self, session, request):
        if False:
            yield None


class _FailingGateway:
    async def execute(self, session, request):
        raise RuntimeError("heartbeat boom")
        if False:
            yield None


class _ConnMgr:
    def __init__(self) -> None:
        self.session_connections: dict[str, set[str]] = {}

    async def broadcast(self, session_id, payload):
        return None


def _write_state(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.asyncio
async def test_process_session_busy_retry_uses_exponential_backoff(monkeypatch, tmp_path):
    import universal_agent.heartbeat_service as hb
    from universal_agent.gateway import GatewaySession

    monkeypatch.setenv("UA_HEARTBEAT_RETRY_BASE_SECONDS", "10")
    monkeypatch.setenv("UA_HEARTBEAT_MAX_RETRY_BACKOFF_SECONDS", "300")
    monkeypatch.setenv("UA_HEARTBEAT_MIN_INTERVAL_SECONDS", "1")

    service = hb.HeartbeatService(_DummyGateway(), _ConnMgr())
    service.default_schedule.every_seconds = 10

    workspace = tmp_path / "ws_busy_retry"
    workspace.mkdir()
    (workspace / "HEARTBEAT.md").write_text("UA_HEARTBEAT_OK", encoding="utf-8")
    state_path = workspace / hb.HEARTBEAT_STATE_FILE

    session = GatewaySession(session_id="hb-busy", user_id="u", workspace_dir=str(workspace), metadata={})
    service.busy_sessions.add(session.session_id)

    _write_state(
        state_path,
        {
            "last_run": time.time() - 60,
            "last_message_hash": None,
            "last_message_ts": 0.0,
            "retry_attempt": 0,
            "next_retry_at": 0.0,
            "retry_reason": None,
            "retry_kind": None,
            "last_retry_delay_seconds": 0.0,
        },
    )

    first_started = time.time()
    await service._process_session(session)
    first_state = hb.HeartbeatState.from_dict(json.loads(state_path.read_text(encoding="utf-8")))

    assert first_state.retry_kind == "busy"
    assert first_state.retry_attempt == 1
    assert first_state.retry_reason == "heartbeat_busy"
    assert first_state.last_retry_delay_seconds == 10.0
    assert first_started + 9 <= first_state.next_retry_at <= first_started + 11.5

    _write_state(
        state_path,
        {
            **first_state.to_dict(),
            "next_retry_at": time.time() - 1,
        },
    )

    second_started = time.time()
    await service._process_session(session)
    second_state = hb.HeartbeatState.from_dict(json.loads(state_path.read_text(encoding="utf-8")))

    assert second_state.retry_kind == "busy"
    assert second_state.retry_attempt == 2
    assert second_state.last_retry_delay_seconds == 20.0
    assert second_started + 19 <= second_state.next_retry_at <= second_started + 21.5


@pytest.mark.asyncio
async def test_run_heartbeat_failure_schedules_exponential_retry(monkeypatch, tmp_path):
    import universal_agent.heartbeat_service as hb
    from universal_agent.gateway import GatewaySession

    monkeypatch.setenv("UA_HEARTBEAT_MOCK_RESPONSE", "0")
    monkeypatch.setenv("UA_HEARTBEAT_RETRY_BASE_SECONDS", "10")
    monkeypatch.setenv("UA_HEARTBEAT_MAX_RETRY_BACKOFF_SECONDS", "300")

    service = hb.HeartbeatService(_FailingGateway(), _ConnMgr())
    service.system_event_provider = lambda _sid: [{"type": "exec_finished", "payload": {}}]
    session = GatewaySession(session_id="hb-failure", user_id="u", workspace_dir=str(tmp_path / "ws"), metadata={})
    state = hb.HeartbeatState()
    state_path = tmp_path / "heartbeat_state.json"

    await service._run_heartbeat(
        session,
        state,
        state_path,
        "Investigate failure",
        service.default_schedule,
        service.default_delivery,
        service.default_visibility,
    )
    persisted_first = hb.HeartbeatState.from_dict(json.loads(state_path.read_text(encoding="utf-8")))

    assert persisted_first.retry_kind == "failure"
    assert persisted_first.retry_attempt == 1
    assert persisted_first.retry_reason == "heartbeat_failed"
    assert persisted_first.last_retry_delay_seconds == 10.0

    state.retry_attempt = persisted_first.retry_attempt
    state.retry_kind = persisted_first.retry_kind
    state.retry_reason = persisted_first.retry_reason
    state.next_retry_at = time.time() - 1
    state.last_retry_delay_seconds = persisted_first.last_retry_delay_seconds

    await service._run_heartbeat(
        session,
        state,
        state_path,
        "Investigate failure",
        service.default_schedule,
        service.default_delivery,
        service.default_visibility,
    )
    persisted_second = hb.HeartbeatState.from_dict(json.loads(state_path.read_text(encoding="utf-8")))

    assert persisted_second.retry_kind == "failure"
    assert persisted_second.retry_attempt == 2
    assert persisted_second.last_retry_delay_seconds == 20.0


@pytest.mark.asyncio
async def test_run_heartbeat_schedules_continuation_after_successful_task_hub_claim(monkeypatch, tmp_path):
    import universal_agent.heartbeat_service as hb
    from universal_agent import task_hub
    from universal_agent.gateway import GatewaySession

    monkeypatch.setenv("UA_HEARTBEAT_MOCK_RESPONSE", "1")
    monkeypatch.setenv("UA_HEARTBEAT_CONTINUATION_DELAY_SECONDS", "1")
    monkeypatch.setenv("UA_HEARTBEAT_MIN_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "activity.db"))
    monkeypatch.setattr(
        task_hub,
        "release_stale_assignments",
        lambda conn, **kwargs: {"stale_detected": 0, "finalized": 0, "reopened": 0},
    )
    monkeypatch.setattr(
        task_hub,
        "get_dispatch_queue",
        lambda conn, **kwargs: {"queue_build_id": "q_test", "eligible_total": 1, "items": []},
    )
    monkeypatch.setattr(
        task_hub,
        "claim_next_dispatch_tasks",
        lambda conn, **kwargs: [{"assignment_id": "asg-1", "task_id": "task-1", "eligible": True}],
    )
    monkeypatch.setattr(
        task_hub,
        "finalize_assignments",
        lambda conn, **kwargs: {
            "finalized": 1,
            "reopened": 0,
            "reviewed": 0,
            "completed": 1,
            "retry_exhausted": 0,
        },
    )

    service = hb.HeartbeatService(_DummyGateway(), _ConnMgr())
    state = hb.HeartbeatState()
    state_path = tmp_path / "heartbeat_state.json"
    session = GatewaySession(session_id="hb-cont", user_id="u", workspace_dir=str(tmp_path / "ws2"), metadata={})

    await service._run_heartbeat(
        session,
        state,
        state_path,
        "UA_HEARTBEAT_OK",
        service.default_schedule,
        service.default_delivery,
        service.default_visibility,
    )
    persisted = hb.HeartbeatState.from_dict(json.loads(state_path.read_text(encoding="utf-8")))

    assert persisted.retry_kind == "continuation"
    assert persisted.retry_attempt == 1
    assert persisted.retry_reason == "task_hub_followup"
    assert persisted.last_retry_delay_seconds == 1.0
    assert persisted.next_retry_at >= persisted.last_run + 0.9
