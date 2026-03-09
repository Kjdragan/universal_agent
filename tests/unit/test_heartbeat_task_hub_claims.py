from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_heartbeat_finalizes_task_hub_claims(monkeypatch, tmp_path):
    import universal_agent.heartbeat_service as hb
    from universal_agent import task_hub
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

    activity_db = tmp_path / "activity_state.db"
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(activity_db))
    monkeypatch.setenv("UA_HEARTBEAT_MOCK_RESPONSE", "1")
    monkeypatch.setenv("UA_HEARTBEAT_OK_TOKENS", "UA_HEARTBEAT_OK")
    monkeypatch.setenv("UA_TASK_HUB_STALE_ASSIGNMENT_SECONDS", "300")
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
        lambda conn, **kwargs: [
            {
                "assignment_id": "asg_test_1",
                "task_id": "task:hb-claim",
                "eligible": True,
            }
        ],
    )

    captured_finalize: dict[str, object] = {}

    def _fake_finalize(conn, *, assignment_ids, state, result_summary, reopen_in_progress=True):
        captured_finalize["assignment_ids"] = list(assignment_ids)
        captured_finalize["state"] = state
        captured_finalize["result_summary"] = result_summary
        captured_finalize["reopen_in_progress"] = reopen_in_progress
        return {"finalized": len(assignment_ids), "reopened": len(assignment_ids)}

    monkeypatch.setattr(task_hub, "finalize_assignments", _fake_finalize)

    service = hb.HeartbeatService(DummyGateway(), DummyCM())
    service.system_event_provider = lambda _sid: [{"type": "seed", "payload": {}}]

    workspace = tmp_path / "ws_finalize"
    workspace.mkdir()
    (workspace / "HEARTBEAT.md").write_text("UA_HEARTBEAT_OK", encoding="utf-8")

    session = GatewaySession(session_id="hb-finalize", user_id="u", workspace_dir=str(workspace), metadata={})
    state = hb.HeartbeatState()
    state_path = workspace / hb.HEARTBEAT_STATE_FILE

    await service._run_heartbeat(
        session,
        state,
        state_path,
        "UA_HEARTBEAT_OK",
        service.default_schedule,
        service.default_delivery,
        service.default_visibility,
    )
    assert captured_finalize["assignment_ids"] == ["asg_test_1"]
    assert captured_finalize["state"] == "completed"
    assert str(captured_finalize["result_summary"]).startswith("heartbeat_run_")
    assert captured_finalize["reopen_in_progress"] is True
