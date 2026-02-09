import os
import json
import shutil
import time
import asyncio
from collections import deque
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from universal_agent import gateway_server
from universal_agent.gateway import GatewaySessionSummary
from universal_agent.gateway import GatewaySession
from universal_agent.cron_service import CronService

# Mock OpsService to avoid full gateway dependency chains in unit tests
# OR rely on the fact that lifespan will init a real OpsService with a real InProcessGateway pointing to tmp_path?
# Let's try to use the real one but pointing to tmp path.

@pytest.fixture
def client(tmp_path, monkeypatch):
    # Patch WORKSPACES_DIR to use tmp_path
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    
    # We must reset the global singletons to force re-init with new path
    monkeypatch.setattr(gateway_server, "_gateway", None)
    monkeypatch.setattr(gateway_server, "_ops_service", None)
    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_session_runtime", {})
    monkeypatch.setattr(gateway_server, "_session_turn_state", {})
    monkeypatch.setattr(gateway_server, "_session_turn_locks", {})
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "_continuity_active_alerts", set())
    monkeypatch.setattr(gateway_server, "_continuity_metric_events", deque(maxlen=5000))
    monkeypatch.setattr(gateway_server, "_calendar_missed_events", {})
    monkeypatch.setattr(gateway_server, "_calendar_missed_notifications", set())
    monkeypatch.setattr(gateway_server, "_calendar_change_proposals", {})
    monkeypatch.setattr(gateway_server, "SCHED_EVENT_PROJECTION_ENABLED", False)
    projection = gateway_server.SchedulingProjectionState(enabled=False)
    event_bus = gateway_server.SchedulingEventBus(max_events=5000)
    event_bus.subscribe(projection.apply_event)
    monkeypatch.setattr(gateway_server, "_scheduling_projection", projection)
    monkeypatch.setattr(gateway_server, "_scheduling_event_bus", event_bus)
    monkeypatch.setattr(
        gateway_server,
        "_observability_metrics",
        {
            "started_at": "2026-02-07T00:00:00",
            "sessions_created": 0,
            "ws_attach_attempts": 0,
            "ws_attach_successes": 0,
            "ws_attach_failures": 0,
            "resume_attempts": 0,
            "resume_successes": 0,
            "resume_failures": 0,
            "turn_busy_rejected": 0,
            "turn_duplicate_in_progress": 0,
            "turn_duplicate_completed": 0,
        },
    )
    monkeypatch.setattr(gateway_server, "_scheduling_runtime_started_ts", time.time())
    monkeypatch.setattr(
        gateway_server,
        "_scheduling_runtime_metrics",
        {
            "started_at": "2026-02-08T00:00:00",
            "counters": {
                "calendar_events_requests": 0,
                "calendar_action_requests": 0,
                "calendar_change_request_requests": 0,
                "calendar_change_confirm_requests": 0,
                "heartbeat_last_requests": 0,
                "heartbeat_wake_requests": 0,
                "event_emissions_total": 0,
                "cron_events_total": 0,
                "heartbeat_events_total": 0,
                "event_bus_published": 0,
                "projection_applied": 0,
                "projection_seed_count": 0,
                "projection_seed_jobs": 0,
                "projection_seed_runs": 0,
                "projection_read_hits": 0,
                "push_replay_requests": 0,
                "push_stream_connects": 0,
                "push_stream_disconnects": 0,
                "push_stream_keepalives": 0,
                "push_stream_event_payloads": 0,
            },
            "event_counts": {
                "cron": {},
                "heartbeat": {},
            },
            "projection": {
                "builds": 0,
                "duration_ms_last": 0.0,
                "duration_ms_max": 0.0,
                "duration_ms_total": 0.0,
                "events_total": 0,
                "always_running_total": 0,
                "stasis_total": 0,
                "due_lag_samples": 0,
                "due_lag_seconds_last": 0.0,
                "due_lag_seconds_max": 0.0,
                "due_lag_seconds_total": 0.0,
            },
        },
    )
    
    # Env vars
    monkeypatch.setenv("UA_GATEWAY_PORT", "0") # Avoid binding real port if it tried
    monkeypatch.setenv("UA_DISABLE_HEARTBEAT", "1")
    monkeypatch.setenv("UA_DISABLE_CRON", "1")
    
    with TestClient(gateway_server.app) as c:
        yield c

def _create_dummy_session(base_dir: Path, session_id: str, logs: list[str]):
    session_dir = base_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    if logs:
        (session_dir / "run.log").write_text("\n".join(logs), encoding="utf-8")
    return session_dir

def test_ops_list_sessions(client, tmp_path):
    # Create some dummy sessions on disk
    _create_dummy_session(tmp_path, "session_A", ["logA"])
    _create_dummy_session(tmp_path, "session_B", ["logB"])
    
    # The gateway lists active sessions (in memory) + discovered (on disk)
    # Our mocked gateway won't have active sessions initially unless we create them via gateway.
    # But list_sessions_async also scans disk.
    
    resp = client.get("/api/v1/ops/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    
    # We should see session_A and session_B
    ids = [s["session_id"] for s in data["sessions"]]
    assert "session_A" in ids
    assert "session_B" in ids

def test_ops_get_session(client, tmp_path):
    _create_dummy_session(tmp_path, "session_details", ["foo"])
    
    resp = client.get("/api/v1/ops/sessions/session_details")
    assert resp.status_code == 200
    data = resp.json()
    assert "session" in data
    assert data["session"]["session_id"] == "session_details"
    assert data["session"]["has_run_log"] is True

def test_ops_delete_session(client, tmp_path):
    _create_dummy_session(tmp_path, "session_del", ["foo"])
    assert (tmp_path / "session_del").exists()
    
    # Missing verify param
    resp = client.delete("/api/v1/ops/sessions/session_del")
    assert resp.status_code == 400
    
    # With verify param
    resp = client.delete("/api/v1/ops/sessions/session_del?confirm=true")
    assert resp.status_code == 200
    assert not (tmp_path / "session_del").exists()

def test_ops_log_tail(client, tmp_path):
    lines = [f"line {i}" for i in range(100)]
    _create_dummy_session(tmp_path, "session_logs", lines)
    
    # Tail default (last 500 lines) -> should get all 100
    resp = client.get("/api/v1/ops/logs/tail?session_id=session_logs")
    assert resp.status_code == 200
    data = resp.json()
    # The new implementation wraps result in "file" too
    assert "lines" in data
    assert len(data["lines"]) == 100
    assert data["lines"][0] == "line 0"
    assert data["lines"][-1] == "line 99"

    # Tail limit
    resp = client.get("/api/v1/ops/logs/tail?session_id=session_logs&limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["lines"]) == 5
    assert data["lines"][-1] == "line 99"
    
    # Tail empty (non existent session -> 400 or empty?)
    # Implementation says if session_id provided, it resolves path.
    # _ops_service.tail_file checks file existence and returns empty dict.
    # BUT ops_logs_tail calls _resolve_workspace_path(session_id) which is just path join.
    # Then tail_file calls exists().
    # So it should return empty struct.
    
    resp = client.get("/api/v1/ops/logs/tail?session_id=non_existent")
    assert resp.status_code == 200 
    data = resp.json()
    assert data["lines"] == []
    assert data["size"] == 0


def test_ops_log_tail_rejects_invalid_session_id(client):
    resp = client.get("/api/v1/ops/logs/tail?session_id=../../etc/passwd")
    assert resp.status_code == 400
    assert "Invalid session id format" in resp.text


def test_ops_log_tail_rejects_path_escape(client):
    resp = client.get("/api/v1/ops/logs/tail?path=../gateway.log")
    assert resp.status_code == 400
    assert "Log path must remain under UA_WORKSPACES_DIR" in resp.text

def test_ops_preview_compact_reset(client, tmp_path):
    # Setup session with logs
    lines = [f"line {i}" for i in range(10)]
    session_dir = _create_dummy_session(tmp_path, "session_complex", lines)
    (session_dir / "activity_journal.log").write_text("\n".join(lines), encoding="utf-8")
    
    # Preview (tails activity journal)
    resp = client.get("/api/v1/ops/sessions/session_complex/preview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "session_complex"
    assert len(data["lines"]) == 10
    
    # Compact
    # Compact run.log to 5 lines
    resp = client.post(
        "/api/v1/ops/sessions/session_complex/compact",
        json={"max_lines": 5, "max_bytes": 1000}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "compacted"
    
    # Verify file on disk
    assert len((session_dir / "run.log").read_text().splitlines()) == 5
    
    # Reset
    # Should move files to archive
    resp = client.post(
        "/api/v1/ops/sessions/session_complex/reset",
        json={"clear_logs": True, "clear_memory": False, "clear_work_products": False}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "reset"
    
    # Verify files gone
    assert not (session_dir / "run.log").exists()
    assert not (session_dir / "activity_journal.log").exists()
    # Archive exists
    archive_dir = Path(resp.json()["archive_dir"])
    assert archive_dir.exists()
    assert (archive_dir / "run.log").exists()


def test_dashboard_summary_and_notifications(client, tmp_path):
    _create_dummy_session(tmp_path, "session_dash", ["ok"])

    summary = client.get("/api/v1/dashboard/summary")
    assert summary.status_code == 200
    payload = summary.json()
    assert "sessions" in payload
    assert "notifications" in payload
    assert payload["sessions"]["total"] >= 1

    list_resp = client.get("/api/v1/dashboard/notifications")
    assert list_resp.status_code == 200
    assert "notifications" in list_resp.json()

    from universal_agent import gateway_server

    gateway_server._notifications.append(  # type: ignore[attr-defined]
        {
            "id": "ntf_test_1",
            "kind": "test",
            "title": "Test",
            "message": "Test notification",
            "session_id": "session_dash",
            "severity": "info",
            "requires_action": False,
            "status": "new",
            "created_at": "2026-02-07T00:00:00",
            "updated_at": "2026-02-07T00:00:00",
            "channels": ["dashboard"],
            "email_targets": [],
            "metadata": {},
        }
    )
    patch_resp = client.patch("/api/v1/dashboard/notifications/ntf_test_1", json={"status": "read"})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["notification"]["status"] == "read"

    snooze_resp = client.patch(
        "/api/v1/dashboard/notifications/ntf_test_1",
        json={"status": "snoozed", "note": "snooze continuity alert"},
    )
    assert snooze_resp.status_code == 200
    snoozed = snooze_resp.json()["notification"]
    assert snoozed["status"] == "snoozed"
    assert snoozed["metadata"]["note"] == "snooze continuity alert"


def test_dashboard_notification_snooze_expiry_reactivates(client):
    from universal_agent import gateway_server
    gateway_server._notifications.append(  # type: ignore[attr-defined]
        {
            "id": "ntf_snooze_expired",
            "kind": "continuity_alert",
            "title": "Continuity Alert",
            "message": "Resume success rate below threshold.",
            "session_id": None,
            "severity": "warning",
            "requires_action": False,
            "status": "snoozed",
            "created_at": "2026-02-07T00:00:00",
            "updated_at": "2026-02-07T00:00:00",
            "channels": ["dashboard"],
            "email_targets": [],
            "metadata": {"snooze_until_ts": 1.0},
        }
    )
    resp = client.get("/api/v1/dashboard/notifications?limit=20")
    assert resp.status_code == 200
    items = resp.json()["notifications"]
    target = next(item for item in items if item["id"] == "ntf_snooze_expired")
    assert target["status"] == "new"
    assert target["metadata"].get("snooze_expired_at")


def test_dashboard_notification_bulk_update(client):
    from universal_agent import gateway_server
    gateway_server._notifications.extend(  # type: ignore[attr-defined]
        [
            {
                "id": "ntf_bulk_1",
                "kind": "continuity_alert",
                "title": "Continuity Alert 1",
                "message": "Alert 1",
                "session_id": None,
                "severity": "warning",
                "requires_action": False,
                "status": "new",
                "created_at": "2026-02-07T00:00:00",
                "updated_at": "2026-02-07T00:00:00",
                "channels": ["dashboard"],
                "email_targets": [],
                "metadata": {},
            },
            {
                "id": "ntf_bulk_2",
                "kind": "continuity_alert",
                "title": "Continuity Alert 2",
                "message": "Alert 2",
                "session_id": None,
                "severity": "warning",
                "requires_action": False,
                "status": "new",
                "created_at": "2026-02-07T00:00:01",
                "updated_at": "2026-02-07T00:00:01",
                "channels": ["dashboard"],
                "email_targets": [],
                "metadata": {},
            },
            {
                "id": "ntf_bulk_other",
                "kind": "mission_complete",
                "title": "Mission",
                "message": "done",
                "session_id": None,
                "severity": "info",
                "requires_action": False,
                "status": "new",
                "created_at": "2026-02-07T00:00:02",
                "updated_at": "2026-02-07T00:00:02",
                "channels": ["dashboard"],
                "email_targets": [],
                "metadata": {},
            },
        ]
    )
    bulk_resp = client.post(
        "/api/v1/dashboard/notifications/bulk",
        json={
            "status": "snoozed",
            "kind": "continuity_alert",
            "current_status": "new",
            "note": "bulk snoozed",
            "snooze_minutes": 30,
            "limit": 100,
        },
    )
    assert bulk_resp.status_code == 200
    payload = bulk_resp.json()
    assert payload["updated"] == 2
    updated_ids = {item["id"] for item in payload["notifications"]}
    assert updated_ids == {"ntf_bulk_1", "ntf_bulk_2"}
    for item in payload["notifications"]:
        assert item["status"] == "snoozed"
        assert item["metadata"]["note"] == "bulk snoozed"
        assert item["metadata"]["snooze_minutes"] == 30

def test_ops_session_continuity_metrics_endpoint(client):
    from universal_agent import gateway_server

    gateway_server._increment_metric("sessions_created")
    gateway_server._increment_metric("resume_attempts")
    gateway_server._increment_metric("resume_successes")
    gateway_server._increment_metric("turn_duplicate_completed", 2)

    resp = client.get("/api/v1/ops/metrics/session-continuity")
    assert resp.status_code == 200
    payload = resp.json()["metrics"]
    assert payload["sessions_created"] == 1
    assert payload["resume_attempts"] == 1
    assert payload["resume_successes"] == 1
    assert payload["turn_duplicate_completed"] == 2
    assert payload["duplicate_turn_prevention_count"] == 2
    assert payload["resume_success_rate"] == 1.0
    assert payload["transport_status"] == "ok"
    assert payload["runtime_status"] == "ok"
    assert payload["window"]["resume_attempts"] == 1
    assert payload["window"]["resume_successes"] == 1
    assert "continuity_status" not in payload
    assert payload["alerts"] == []


def test_ops_session_continuity_metrics_alerts(client):
    from universal_agent import gateway_server

    gateway_server._increment_metric("resume_attempts", 10)
    gateway_server._increment_metric("resume_successes", 6)
    gateway_server._increment_metric("resume_failures", 4)
    gateway_server._increment_metric("ws_attach_attempts", 5)
    gateway_server._increment_metric("ws_attach_successes", 2)
    gateway_server._increment_metric("ws_attach_failures", 3)

    resp = client.get("/api/v1/ops/metrics/session-continuity")
    assert resp.status_code == 200
    payload = resp.json()["metrics"]
    assert payload["transport_status"] == "degraded"
    assert payload["runtime_status"] == "ok"
    assert "continuity_status" not in payload
    codes = {item["code"] for item in payload["alerts"]}
    assert "resume_success_rate_low" in codes
    assert "attach_success_rate_low" in codes
    assert "resume_failures_high" in codes
    assert "attach_failures_high" in codes


def test_ops_session_continuity_metrics_use_rolling_window(client):
    from universal_agent import gateway_server

    gateway_server._continuity_metric_events.clear()
    now_ts = time.time()
    old_ts = now_ts - (gateway_server.CONTINUITY_WINDOW_SECONDS + 5)
    # Old failures outside the rolling window should not degrade current status.
    gateway_server._record_continuity_metric_event("resume_failures", amount=3, ts=old_ts)
    gateway_server._record_continuity_metric_event("ws_attach_failures", amount=3, ts=old_ts)
    gateway_server._sync_continuity_notifications()

    resp = client.get("/api/v1/ops/metrics/session-continuity")
    assert resp.status_code == 200
    payload = resp.json()["metrics"]
    assert payload["transport_status"] == "ok"
    assert "continuity_status" not in payload
    assert payload["window"]["resume_failures"] == 0
    assert payload["window"]["ws_attach_failures"] == 0
    assert payload["alerts"] == []


def test_ops_scheduling_runtime_metrics_endpoint(client):
    from universal_agent import gateway_server

    calendar_resp = client.get("/api/v1/ops/calendar/events?source=all&view=week")
    assert calendar_resp.status_code == 200

    gateway_server._scheduling_record_event("cron", "cron_job_created")
    gateway_server._scheduling_record_event("heartbeat", "heartbeat_started")

    resp = client.get("/api/v1/ops/metrics/scheduling-runtime")
    assert resp.status_code == 200
    metrics = resp.json()["metrics"]

    counters = metrics["counters"]
    projection = metrics["projection"]
    assert counters["calendar_events_requests"] >= 1
    assert counters["event_emissions_total"] == 2
    assert counters["cron_events_total"] == 1
    assert counters["heartbeat_events_total"] == 1
    assert metrics["event_counts"]["cron"]["cron_job_created"] == 1
    assert metrics["event_counts"]["heartbeat"]["heartbeat_started"] == 1
    assert projection["builds"] >= 1
    assert projection["duration_ms_last"] >= 0.0
    assert projection["duration_ms_avg"] >= 0.0
    assert metrics["uptime_seconds"] >= 0.0


def test_ops_scheduling_events_replay_and_stream(client):
    from universal_agent import gateway_server

    published = gateway_server._scheduling_event_bus.publish(
        "cron",
        "cron_job_created",
        {"job": {"job_id": "job_stream_test", "command": "echo hi"}},
    )
    assert int(published.get("seq", 0)) >= 1

    replay_resp = client.get("/api/v1/ops/scheduling/events?since_seq=0&limit=50")
    assert replay_resp.status_code == 200
    replay_payload = replay_resp.json()
    events = replay_payload["events"]
    assert any(item.get("type") == "cron_job_created" for item in events)
    assert "projection_version" in replay_payload
    assert "projection_last_event_seq" in replay_payload
    assert "event_bus_seq" in replay_payload

    stream_resp = client.get("/api/v1/ops/scheduling/stream?since_seq=0&heartbeat_seconds=2&limit=20&once=1")
    assert stream_resp.status_code == 200
    assert stream_resp.headers["content-type"].startswith("text/event-stream")
    lines = [line for line in stream_resp.text.splitlines() if line.startswith("data:")]
    assert lines
    payload = json.loads(lines[0][len("data:"):].strip())
    kind = payload.get("kind")
    assert kind in {"event", "heartbeat"}
    if kind == "event":
        event = payload.get("event") or {}
        assert event.get("type") == "cron_job_created"
    assert "projection_version" in payload

    metrics_resp = client.get("/api/v1/ops/metrics/scheduling-runtime")
    assert metrics_resp.status_code == 200
    counters = metrics_resp.json()["metrics"]["counters"]
    assert counters["push_replay_requests"] >= 1
    assert counters["push_stream_connects"] >= 1
    assert counters["push_stream_disconnects"] >= 1
    assert (counters["push_stream_event_payloads"] + counters["push_stream_keepalives"]) >= 1


def test_ops_scheduling_stream_disabled_returns_503(client, monkeypatch):
    from universal_agent import gateway_server

    monkeypatch.setattr(gateway_server, "SCHED_PUSH_ENABLED", False)
    resp = client.get("/api/v1/ops/scheduling/stream?once=1")
    assert resp.status_code == 503
    assert "disabled" in str(resp.json().get("detail", "")).lower()


def test_scheduling_projection_idempotent_and_order_tolerant():
    from universal_agent import gateway_server

    state = gateway_server.SchedulingProjectionState(enabled=True)
    run = {
        "run_id": "run_1",
        "job_id": "job_1",
        "status": "success",
        "scheduled_at": time.time(),
    }
    job = {
        "job_id": "job_1",
        "user_id": "owner_1",
        "workspace_dir": "/tmp/ws",
        "command": "echo hi",
        "enabled": True,
        "every_seconds": 60,
        "cron_expr": None,
        "run_at": None,
        "created_at": time.time(),
        "metadata": {},
    }

    # Out-of-order event application: run arrives before job create.
    state.apply_event(
        {"seq": 1, "source": "cron", "type": "cron_run_completed", "timestamp": "2026-02-08T00:00:00", "data": {"run": run}}
    )
    # Duplicate run event should be idempotent.
    state.apply_event(
        {"seq": 2, "source": "cron", "type": "cron_run_completed", "timestamp": "2026-02-08T00:00:01", "data": {"run": run}}
    )
    state.apply_event(
        {"seq": 3, "source": "cron", "type": "cron_job_created", "timestamp": "2026-02-08T00:00:02", "data": {"job": job}}
    )

    jobs = state.list_cron_jobs()
    runs = state.list_cron_runs(limit=100)
    assert len(jobs) == 1
    assert jobs[0].job_id == "job_1"
    assert len([item for item in runs if item.get("job_id") == "job_1"]) == 1


def test_calendar_projection_feed_parity(client, tmp_path):
    from universal_agent import gateway_server

    gateway_server._cron_service = CronService(
        gateway_server.get_gateway(),
        tmp_path,
    )
    cron_ws = tmp_path / "cron_projection_workspace"
    cron_ws.mkdir(parents=True, exist_ok=True)
    gateway_server._cron_service.add_job(
        user_id="calendar_owner",
        workspace_dir=str(cron_ws),
        command="projection parity check",
        every_raw="30m",
        enabled=True,
    )

    gateway_server._scheduling_projection.reset()
    gateway_server._scheduling_projection.enabled = False
    baseline_resp = client.get("/api/v1/ops/calendar/events?source=cron&view=week")
    assert baseline_resp.status_code == 200
    baseline_events = [item for item in baseline_resp.json()["events"] if item.get("source") == "cron"]
    baseline_pairs = {(item["event_id"], item["status"]) for item in baseline_events}

    gateway_server._scheduling_projection.reset()
    gateway_server._scheduling_projection.enabled = True
    gateway_server._scheduling_projection.seed_from_runtime()
    projected_resp = client.get("/api/v1/ops/calendar/events?source=cron&view=week")
    assert projected_resp.status_code == 200
    projected_events = [item for item in projected_resp.json()["events"] if item.get("source") == "cron"]
    projected_pairs = {(item["event_id"], item["status"]) for item in projected_events}
    assert projected_pairs == baseline_pairs

    metrics_resp = client.get("/api/v1/ops/metrics/scheduling-runtime")
    assert metrics_resp.status_code == 200
    metrics = metrics_resp.json()["metrics"]
    assert metrics["projection_state"]["enabled"] is True
    assert metrics["projection_state"]["seeded"] is True
    assert metrics["counters"]["projection_read_hits"] >= 1


def test_continuity_alert_notifications_are_emitted_and_deduped(client):
    from universal_agent import gateway_server

    gateway_server._increment_metric("resume_failures", 3)
    alert_notifications = [item for item in gateway_server._notifications if item.get("kind") == "continuity_alert"]
    assert len(alert_notifications) == 1
    assert alert_notifications[0]["metadata"]["code"] == "resume_failures_high"

    # Condition still active; no duplicate alert should be emitted.
    gateway_server._increment_metric("resume_failures", 1)
    alert_notifications_2 = [item for item in gateway_server._notifications if item.get("kind") == "continuity_alert"]
    assert len(alert_notifications_2) == 1

    # Force recovery and sync to emit resolved notification.
    gateway_server._observability_metrics["resume_failures"] = 0
    gateway_server._continuity_metric_events.clear()
    gateway_server._sync_continuity_notifications()
    recovered = [item for item in gateway_server._notifications if item.get("kind") == "continuity_recovered"]
    assert len(recovered) == 1
    assert recovered[0]["metadata"]["code"] == "resume_failures_high"


def test_session_policy_endpoints_and_resume(client, tmp_path):
    session_dir = _create_dummy_session(tmp_path, "session_policy", ["run"])
    session = GatewaySession(
        session_id="session_policy",
        user_id="tester",
        workspace_dir=str(session_dir),
        metadata={},
    )
    gateway_server._sessions["session_policy"] = session
    gateway_server._pending_gated_requests["session_policy"] = {
        "session_id": "session_policy",
        "approval_id": "approval_test_1",
        "status": "pending",
        "request": {"user_input": "send an email", "force_complex": False, "metadata": {}},
    }

    get_resp = client.get("/api/v1/sessions/session_policy/policy")
    assert get_resp.status_code == 200
    policy = get_resp.json()["policy"]
    assert policy["identity_mode"] == "persona"
    assert policy["memory"]["mode"] == "session_only"

    patch_resp = client.patch(
        "/api/v1/sessions/session_policy/policy",
        json={
            "patch": {
                "identity_mode": "operator_proxy",
                "autonomy_mode": "guarded",
                "memory": {
                    "mode": "selective",
                    "tags": ["dev_test", "retain"],
                    "long_term_tag_allowlist": ["retain"],
                    "session_memory_enabled": True,
                },
            }
        },
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["policy"]["identity_mode"] == "operator_proxy"
    assert patch_resp.json()["policy"]["memory"]["mode"] == "selective"
    assert patch_resp.json()["policy"]["memory"]["long_term_tag_allowlist"] == ["retain"]

    pending_resp = client.get("/api/v1/sessions/session_policy/pending")
    assert pending_resp.status_code == 200
    assert pending_resp.json()["pending"]["approval_id"] == "approval_test_1"

    resume_resp = client.post(
        "/api/v1/sessions/session_policy/resume",
        json={"approval_id": "approval_test_1"},
    )
    assert resume_resp.status_code == 200
    assert resume_resp.json()["pending"]["status"] == "approved"


def test_ops_session_runtime_state_fields(client, tmp_path):
    session_dir = _create_dummy_session(tmp_path, "session_runtime", ["run"])
    session = GatewaySession(
        session_id="session_runtime",
        user_id="tester",
        workspace_dir=str(session_dir),
        metadata={},
    )
    gateway_server.get_gateway()._sessions["session_runtime"] = session
    gateway_server.store_session(session)

    gateway_server._set_session_connections("session_runtime", 2)
    gateway_server._increment_session_active_runs("session_runtime")
    gateway_server._record_session_event("session_runtime", "status")

    detail_running = client.get("/api/v1/ops/sessions/session_runtime")
    assert detail_running.status_code == 200
    running_payload = detail_running.json()["session"]
    assert running_payload["status"] == "running"
    assert running_payload["active_connections"] == 2
    assert running_payload["active_runs"] == 1
    assert running_payload["last_event_seq"] >= 1

    gateway_server._decrement_session_active_runs("session_runtime")
    gateway_server._set_session_connections("session_runtime", 0)
    detail_idle = client.get("/api/v1/ops/sessions/session_runtime")
    assert detail_idle.status_code == 200
    idle_payload = detail_idle.json()["session"]
    assert idle_payload["status"] == "idle"
    assert idle_payload["active_connections"] == 0


def test_ops_session_filters_source_owner_status(client, tmp_path):
    tg_dir = _create_dummy_session(tmp_path, "tg_12345", ["tg"])
    web_dir = _create_dummy_session(tmp_path, "session_web", ["web"])

    session = GatewaySession(
        session_id="session_web",
        user_id="tester_user",
        workspace_dir=str(web_dir),
        metadata={},
    )
    gateway_server.get_gateway()._sessions["session_web"] = session
    gateway_server.store_session(session)
    gateway_server._increment_session_active_runs("session_web")

    all_resp = client.get("/api/v1/ops/sessions")
    assert all_resp.status_code == 200
    all_items = all_resp.json()["sessions"]
    assert any(item["source"] == "telegram" for item in all_items)
    assert any(item["owner"] == "tester_user" for item in all_items)

    running_resp = client.get("/api/v1/ops/sessions?status=running")
    assert running_resp.status_code == 200
    running_ids = {item["session_id"] for item in running_resp.json()["sessions"]}
    assert "session_web" in running_ids
    assert "tg_12345" not in running_ids

    tg_resp = client.get("/api/v1/ops/sessions?source=telegram")
    assert tg_resp.status_code == 200
    tg_ids = {item["session_id"] for item in tg_resp.json()["sessions"]}
    assert tg_ids == {"tg_12345"}

    owner_resp = client.get("/api/v1/ops/sessions?owner=tester_user")
    assert owner_resp.status_code == 200
    owner_ids = {item["session_id"] for item in owner_resp.json()["sessions"]}
    assert owner_ids == {"session_web"}


def test_ops_session_filters_memory_mode(client, tmp_path):
    full_dir = _create_dummy_session(tmp_path, "session_full_mem", ["m1"])
    default_dir = _create_dummy_session(tmp_path, "session_default_mem", ["m2"])
    (full_dir / "session_policy.json").write_text(
        '{"memory":{"mode":"full"},"user_id":"tester_full"}',
        encoding="utf-8",
    )
    (default_dir / "session_policy.json").write_text(
        '{"memory":{"mode":"session_only"},"user_id":"tester_default"}',
        encoding="utf-8",
    )

    full_resp = client.get("/api/v1/ops/sessions?memory_mode=full")
    assert full_resp.status_code == 200
    full_ids = {item["session_id"] for item in full_resp.json()["sessions"]}
    assert "session_full_mem" in full_ids
    assert "session_default_mem" not in full_ids

    for item in full_resp.json()["sessions"]:
        if item["session_id"] == "session_full_mem":
            assert item["memory_mode"] == "full"
            break
    else:
        raise AssertionError("session_full_mem should be present in filtered response")


def test_ops_session_archive_and_cancel_actions(client, tmp_path):
    session_dir = _create_dummy_session(tmp_path, "session_actions", ["line 1", "line 2"])
    (session_dir / "activity_journal.log").write_text("entry\n", encoding="utf-8")
    session = GatewaySession(
        session_id="session_actions",
        user_id="tester",
        workspace_dir=str(session_dir),
        metadata={"run_id": "run_test_123"},
    )
    gateway_server.get_gateway()._sessions["session_actions"] = session
    gateway_server.store_session(session)

    archive_resp = client.post(
        "/api/v1/ops/sessions/session_actions/archive",
        json={"clear_memory": False, "clear_work_products": False},
    )
    assert archive_resp.status_code == 200
    assert archive_resp.json()["status"] == "archived"
    assert not (session_dir / "run.log").exists()

    cancel_resp = client.post(
        "/api/v1/ops/sessions/session_actions/cancel",
        json={"reason": "ops cancel test"},
    )
    assert cancel_resp.status_code == 200
    payload = cancel_resp.json()
    assert payload["status"] == "cancel_requested"
    assert payload["session_id"] == "session_actions"
    assert payload["reason"] == "ops cancel test"


def test_ops_cancel_outstanding_runs(client, tmp_path):
    running_dir = _create_dummy_session(tmp_path, "session_running", ["line 1"])
    _create_dummy_session(tmp_path, "session_idle", ["line 1"])

    running_session = GatewaySession(
        session_id="session_running",
        user_id="tester",
        workspace_dir=str(running_dir),
        metadata={
            "run_id": "run_bulk_cancel_123",
        },
    )
    gateway_server.get_gateway()._sessions["session_running"] = running_session
    gateway_server.store_session(running_session)
    gateway_server._increment_session_active_runs("session_running")

    cancel_resp = client.post(
        "/api/v1/ops/sessions/cancel",
        json={"reason": "bulk cancel test"},
    )
    assert cancel_resp.status_code == 200
    payload = cancel_resp.json()
    assert payload["status"] == "cancel_requested"
    assert payload["reason"] == "bulk cancel test"
    assert payload["sessions_considered"] == 1
    assert payload["sessions_cancelled"] == ["session_running"]


def test_ops_calendar_events_with_cron_missed_stasis(client, tmp_path):
    gateway_server._cron_service = CronService(
        gateway_server.get_gateway(),
        tmp_path,
    )
    cron_ws = tmp_path / "cron_workspace"
    cron_ws.mkdir(parents=True, exist_ok=True)
    gateway_server._cron_service.add_job(
        user_id="calendar_owner",
        workspace_dir=str(cron_ws),
        command="heartbeat check",
        run_at=time.time() - 300,
        enabled=True,
    )

    resp = client.get("/api/v1/ops/calendar/events?source=cron&view=week")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["events"]
    assert any(item["source"] == "cron" for item in payload["events"])
    assert any(item["status"] == "missed" for item in payload["events"])
    assert payload["stasis_queue"]


def test_ops_calendar_event_actions_for_cron(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UA_CRON_MOCK_RESPONSE", "1")
    gateway_server._cron_service = CronService(
        gateway_server.get_gateway(),
        tmp_path,
    )
    cron_ws = tmp_path / "cron_actions_workspace"
    cron_ws.mkdir(parents=True, exist_ok=True)
    job = gateway_server._cron_service.add_job(
        user_id="calendar_owner",
        workspace_dir=str(cron_ws),
        command="run status report",
        every_raw="30m",
        enabled=True,
    )
    event_id = f"cron|{job.job_id}|{int(job.next_run_at or time.time())}"

    pause_resp = client.post(
        f"/api/v1/ops/calendar/events/{event_id}/action",
        json={"action": "pause"},
    )
    assert pause_resp.status_code == 200
    assert pause_resp.json()["job"]["enabled"] is False

    resume_resp = client.post(
        f"/api/v1/ops/calendar/events/{event_id}/action",
        json={"action": "resume"},
    )
    assert resume_resp.status_code == 200
    assert resume_resp.json()["job"]["enabled"] is True

    run_resp = client.post(
        f"/api/v1/ops/calendar/events/{event_id}/action",
        json={"action": "run_now"},
    )
    assert run_resp.status_code == 200
    assert run_resp.json()["run"]["status"] == "success"


def test_ops_calendar_missed_stasis_actions_for_cron(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UA_CRON_MOCK_RESPONSE", "1")
    gateway_server._cron_service = CronService(
        gateway_server.get_gateway(),
        tmp_path,
    )
    cron_ws = tmp_path / "cron_stasis_actions_workspace"
    cron_ws.mkdir(parents=True, exist_ok=True)

    job_approve = gateway_server._cron_service.add_job(
        user_id="calendar_owner",
        workspace_dir=str(cron_ws),
        command="approve missed run",
        run_at=time.time() - 900,
        enabled=True,
    )
    job_reschedule = gateway_server._cron_service.add_job(
        user_id="calendar_owner",
        workspace_dir=str(cron_ws),
        command="reschedule missed run",
        run_at=time.time() - 1200,
        enabled=True,
    )
    job_delete = gateway_server._cron_service.add_job(
        user_id="calendar_owner",
        workspace_dir=str(cron_ws),
        command="delete missed run",
        run_at=time.time() - 1500,
        enabled=True,
    )

    feed_resp = client.get("/api/v1/ops/calendar/events?source=cron&view=week")
    assert feed_resp.status_code == 200
    feed = feed_resp.json()
    missed_by_job = {
        str(item["source_ref"]): item
        for item in feed["events"]
        if item.get("source") == "cron" and item.get("status") == "missed"
    }
    assert job_approve.job_id in missed_by_job
    assert job_reschedule.job_id in missed_by_job
    assert job_delete.job_id in missed_by_job

    approve_event_id = missed_by_job[job_approve.job_id]["event_id"]
    reschedule_event_id = missed_by_job[job_reschedule.job_id]["event_id"]
    delete_event_id = missed_by_job[job_delete.job_id]["event_id"]

    approve_resp = client.post(
        f"/api/v1/ops/calendar/events/{approve_event_id}/action",
        json={"action": "approve_backfill_run"},
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["run"]["status"] == "success"

    reschedule_resp = client.post(
        f"/api/v1/ops/calendar/events/{reschedule_event_id}/action",
        json={"action": "reschedule", "run_at": "in 15m"},
    )
    assert reschedule_resp.status_code == 200
    assert reschedule_resp.json()["job"]["delete_after_run"] is True

    delete_resp = client.post(
        f"/api/v1/ops/calendar/events/{delete_event_id}/action",
        json={"action": "delete_missed"},
    )
    assert delete_resp.status_code == 200
    assert delete_resp.json()["status"] == "ok"

    refreshed_resp = client.get("/api/v1/ops/calendar/events?source=cron&view=week")
    assert refreshed_resp.status_code == 200
    refreshed = refreshed_resp.json()

    # Approve-backfill should reconcile the originally missed event to success.
    approved_event = next((item for item in refreshed["events"] if item["event_id"] == approve_event_id), None)
    assert approved_event is not None
    assert approved_event["status"] == "success"

    # Rescheduled/deleted missed events should no longer reappear as active missed items.
    assert not any(item["event_id"] == reschedule_event_id for item in refreshed["events"])
    assert not any(item["event_id"] == delete_event_id for item in refreshed["events"])
    assert not any(item.get("event_id") in {approve_event_id, reschedule_event_id, delete_event_id} for item in refreshed["stasis_queue"])


def test_cron_default_wake_for_session_bound_jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_CRON_MOCK_RESPONSE", "1")
    wake_calls: list[tuple[str, str, str]] = []

    def wake_callback(session_id: str, mode: str, reason: str) -> None:
        wake_calls.append((session_id, mode, reason))

    service = CronService(
        gateway_server.get_gateway(),
        tmp_path,
        wake_callback=wake_callback,
    )
    job_default = service.add_job(
        user_id="calendar_owner",
        workspace_dir=str(tmp_path / "cron_wake_default_workspace"),
        command="default wake",
        every_raw="30m",
        enabled=True,
        metadata={"session_id": "sess_default"},
    )
    job_disabled = service.add_job(
        user_id="calendar_owner",
        workspace_dir=str(tmp_path / "cron_wake_disabled_workspace"),
        command="disabled wake",
        every_raw="30m",
        enabled=True,
        metadata={"session_id": "sess_disabled", "wake_heartbeat": "off"},
    )

    run_default = asyncio.run(service.run_job_now(job_default.job_id, reason="test_default_wake"))
    assert run_default.status == "success"
    run_disabled = asyncio.run(service.run_job_now(job_disabled.job_id, reason="test_disabled_wake"))
    assert run_disabled.status == "success"

    assert any(session_id == "sess_default" and mode == "next" for session_id, mode, _ in wake_calls)
    assert not any(session_id == "sess_disabled" for session_id, _, _ in wake_calls)


def test_ops_calendar_change_request_confirm_for_cron_interval(client, tmp_path):
    gateway_server._cron_service = CronService(
        gateway_server.get_gateway(),
        tmp_path,
    )
    cron_ws = tmp_path / "cron_change_workspace"
    cron_ws.mkdir(parents=True, exist_ok=True)
    job = gateway_server._cron_service.add_job(
        user_id="calendar_owner",
        workspace_dir=str(cron_ws),
        command="weekly summary",
        every_raw="30m",
        enabled=True,
    )
    event_id = f"cron|{job.job_id}|{int(job.next_run_at or time.time())}"

    propose_resp = client.post(
        f"/api/v1/ops/calendar/events/{event_id}/change-request",
        json={"instruction": "Change this to every 45 minutes"},
    )
    assert propose_resp.status_code == 200
    proposal = propose_resp.json()["proposal"]
    assert proposal["status"] == "pending_confirmation"
    assert proposal["operation"]["type"] == "cron_set_interval"

    confirm_resp = client.post(
        f"/api/v1/ops/calendar/events/{event_id}/change-request/confirm",
        json={"proposal_id": proposal["proposal_id"], "approve": True},
    )
    assert confirm_resp.status_code == 200
    assert confirm_resp.json()["status"] == "applied"
    updated = gateway_server._cron_service.get_job(job.job_id)
    assert updated is not None
    assert updated.every_seconds == 45 * 60
