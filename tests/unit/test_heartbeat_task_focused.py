"""R4: tests for the task-focused lean-tick activation (_resolve_task_focused) and
the deterministic (zero-LLM-cost) findings JSON it writes.

`_is_task_focused` shipped hardcoded to `False` in commit ae82c81c (2026-05-23),
alongside the move of dispatch-claim logic to `todo_dispatch_service` (which left
`task_hub_claimed` always `[]` at the `_run_heartbeat` call site — see
`test_heartbeat_retry_queue.py::test_run_heartbeat_completes_without_continuation_when_dispatch_moved`).
This restores the intended claims-conditional activation behind an env
kill-switch. Because `task_hub_claimed` is still always `[]` in production today,
the "deterministic findings JSON on a task-focused tick" coverage below drives
`_is_task_focused=True` by monkeypatching `_resolve_task_focused` directly
(dependency injection at the one seam the real code calls through) rather than
via a real non-empty claims list, which the current call site cannot produce.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


class _DummyGateway:
    async def execute(self, session, request):
        if False:
            yield None


class _CapturingGateway:
    """Records every GatewayRequest passed to execute(), never yields events."""

    def __init__(self) -> None:
        self.requests: list = []

    async def execute(self, session, request):
        self.requests.append(request)
        if False:
            yield None


class _ConnMgr:
    def __init__(self) -> None:
        self.session_connections: dict[str, set[str]] = {}

    async def broadcast(self, session_id, payload):
        return None


# ── _resolve_task_focused resolution ────────────────────────────────────────


def test_resolve_task_focused_true_when_claims_present_and_no_env(monkeypatch):
    from universal_agent.heartbeat_service import _resolve_task_focused

    monkeypatch.delenv("UA_HEARTBEAT_TASK_FOCUSED", raising=False)
    assert _resolve_task_focused([{"task_id": "t1"}]) is True


def test_resolve_task_focused_false_when_no_claims(monkeypatch):
    from universal_agent.heartbeat_service import _resolve_task_focused

    monkeypatch.delenv("UA_HEARTBEAT_TASK_FOCUSED", raising=False)
    assert _resolve_task_focused([]) is False


def test_resolve_task_focused_false_when_no_claims_even_if_env_enabled(monkeypatch):
    from universal_agent.heartbeat_service import _resolve_task_focused

    monkeypatch.setenv("UA_HEARTBEAT_TASK_FOCUSED", "1")
    assert _resolve_task_focused([]) is False


@pytest.mark.parametrize("kill_value", ["0", "false", "False", "no", "off"])
def test_resolve_task_focused_kill_switch_disables_with_claims(monkeypatch, kill_value):
    from universal_agent.heartbeat_service import _resolve_task_focused

    monkeypatch.setenv("UA_HEARTBEAT_TASK_FOCUSED", kill_value)
    assert _resolve_task_focused([{"task_id": "t1"}]) is False


@pytest.mark.parametrize("enable_value", ["1", "true", "True", "yes", "on"])
def test_resolve_task_focused_true_with_claims_and_explicit_enable(monkeypatch, enable_value):
    from universal_agent.heartbeat_service import _resolve_task_focused

    monkeypatch.setenv("UA_HEARTBEAT_TASK_FOCUSED", enable_value)
    assert _resolve_task_focused([{"task_id": "t1"}]) is True


# ── Deterministic findings JSON on a task-focused tick ──────────────────────


@pytest.mark.asyncio
async def test_task_focused_tick_writes_deterministic_findings_json(monkeypatch, tmp_path):
    """Production Verification Rule 2: a real artifact on real disk. When
    `_is_task_focused` resolves True, `_run_heartbeat` must write
    work_products/heartbeat_findings_latest.json via the Python-deterministic
    path (source='task_run'), with zero LLM findings authored."""
    from universal_agent import task_hub
    from universal_agent.gateway import GatewaySession
    import universal_agent.heartbeat_service as hb

    monkeypatch.setenv("UA_HEARTBEAT_MOCK_RESPONSE", "1")
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
        "finalize_assignments",
        lambda conn, **kwargs: {
            "finalized": 1,
            "reopened": 0,
            "reviewed": 0,
            "completed": 1,
            "retry_exhausted": 0,
        },
    )
    # Force the lean path on regardless of the (currently always-empty)
    # task_hub_claimed local — the one seam production code calls through.
    monkeypatch.setattr(hb, "_resolve_task_focused", lambda claims: True)

    service = hb.HeartbeatService(_DummyGateway(), _ConnMgr())
    state = hb.HeartbeatState()
    state_path = tmp_path / "heartbeat_state.json"
    workspace = tmp_path / "ws_task_focused"
    session = GatewaySession(session_id="hb-task-focused", user_id="u", workspace_dir=str(workspace), metadata={})

    await service._run_heartbeat(
        session,
        state,
        state_path,
        "UA_HEARTBEAT_OK",
        service.default_schedule,
        service.default_delivery,
        service.default_visibility,
    )

    findings_path = workspace / "work_products" / "heartbeat_findings_latest.json"
    assert findings_path.exists(), "task-focused tick must write heartbeat_findings_latest.json deterministically"
    payload = json.loads(findings_path.read_text(encoding="utf-8"))
    assert payload["source"] == "task_run"
    assert payload["findings"] == []
    assert "task_run" in payload


# ── Routing invariant (Part 3): metadata.source is always "heartbeat" ───────


@pytest.mark.asyncio
@pytest.mark.parametrize("force_task_focused", [False, True])
async def test_heartbeat_gateway_request_always_carries_source_heartbeat(
    monkeypatch, tmp_path, force_task_focused
):
    """Regression guard for Finding 1b: classification safety hinges on
    metadata['source'] == 'heartbeat' being preserved on every GatewayRequest
    the heartbeat builds, regardless of task_focused. If a future lean path
    forgets to set it, classify_query can fall through to ROUTE_SIMPLE (no
    tools) on a tick that needs task_hub_task_action/vp_dispatch_mission."""
    from universal_agent import task_hub
    from universal_agent.gateway import GatewaySession
    import universal_agent.heartbeat_service as hb

    monkeypatch.delenv("UA_HEARTBEAT_MOCK_RESPONSE", raising=False)
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
        lambda conn, **kwargs: {"queue_build_id": "q_test", "eligible_total": 0, "items": []},
    )
    monkeypatch.setattr(
        task_hub,
        "finalize_assignments",
        lambda conn, **kwargs: {
            "finalized": 0,
            "reopened": 0,
            "reviewed": 0,
            "completed": 0,
            "retry_exhausted": 0,
        },
    )
    monkeypatch.setattr(hb, "_resolve_task_focused", lambda claims: force_task_focused)

    gateway = _CapturingGateway()
    service = hb.HeartbeatService(gateway, _ConnMgr())
    state = hb.HeartbeatState()
    state_path = tmp_path / "heartbeat_state.json"
    workspace = tmp_path / "ws_source_invariant"
    session = GatewaySession(session_id="hb-source-inv", user_id="u", workspace_dir=str(workspace), metadata={})

    await service._run_heartbeat(
        session,
        state,
        state_path,
        "Investigate something",
        service.default_schedule,
        service.default_delivery,
        service.default_visibility,
    )

    assert gateway.requests, "expected the heartbeat to build and execute a GatewayRequest"
    for request in gateway.requests:
        assert request.metadata.get("source") == "heartbeat"
