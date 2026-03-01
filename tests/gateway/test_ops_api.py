import os
import json
import io
import shutil
import time
import asyncio
import sqlite3
import tarfile
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from universal_agent import gateway_server
from universal_agent.ops_service import OpsService
from universal_agent.gateway import InProcessGateway
from universal_agent.gateway import GatewaySessionSummary
from universal_agent.gateway import GatewaySession
from universal_agent.cron_service import CronService
from universal_agent.durable.state import (
    append_vp_event,
    append_vp_session_event,
    get_vp_mission,
    get_vp_bridge_cursor,
    list_vp_events,
    upsert_vp_mission,
    upsert_vp_session,
)
from universal_agent.vp.clients.base import MissionOutcome, VpClient
from universal_agent.vp.worker_loop import VpWorkerLoop
from universal_agent.runtime_role import build_factory_runtime_policy

# Mock OpsService to avoid full gateway dependency chains in unit tests
# OR rely on the fact that lifespan will init a real OpsService with a real InProcessGateway pointing to tmp_path?
# Let's try to use the real one but pointing to tmp path.

@pytest.fixture
def client(tmp_path, monkeypatch):
    # Patch WORKSPACES_DIR to use tmp_path
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str((tmp_path / "runtime_state.db").resolve()))
    monkeypatch.setenv("UA_CODER_VP_DB_PATH", str((tmp_path / "coder_vp_state.db").resolve()))
    monkeypatch.setenv("UA_VP_DB_PATH", str((tmp_path / "vp_state.db").resolve()))
    
    # We must reset the global singletons to force re-init with new path
    monkeypatch.setattr(gateway_server, "_gateway", None)
    monkeypatch.setattr(gateway_server, "_ops_service", None)
    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_session_runtime", {})
    monkeypatch.setattr(gateway_server, "_session_turn_state", {})
    monkeypatch.setattr(gateway_server, "_session_turn_locks", {})
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "_tutorial_bootstrap_jobs", {})
    monkeypatch.setattr(gateway_server, "_tutorial_bootstrap_queue", deque())
    monkeypatch.setattr(gateway_server, "_delegation_mission_bus", None)
    monkeypatch.setattr(
        gateway_server,
        "_delegation_metrics",
        {
            "redis_enabled": False,
            "connected": False,
            "last_error": None,
            "last_publish_at": None,
            "published_total": 0,
        },
    )
    monkeypatch.setattr(gateway_server, "_factory_registrations", {})
    monkeypatch.setattr(gateway_server, "_continuity_active_alerts", set())
    monkeypatch.setattr(gateway_server, "_continuity_metric_events", deque(maxlen=5000))
    monkeypatch.setattr(gateway_server, "_calendar_missed_events", {})
    monkeypatch.setattr(gateway_server, "_calendar_missed_notifications", set())
    monkeypatch.setattr(gateway_server, "_calendar_change_proposals", {})
    monkeypatch.setattr(gateway_server, "_vp_event_bridge_task", None)
    monkeypatch.setattr(gateway_server, "_vp_event_bridge_stop_event", None)
    monkeypatch.setattr(gateway_server, "_vp_event_bridge_last_rowid", 0)
    monkeypatch.setattr(
        gateway_server,
        "_vp_event_bridge_metrics",
        {
            "cycles": 0,
            "events_bridged_total": 0,
            "events_bridged_last": 0,
            "errors": 0,
            "last_error": None,
            "last_run_at": None,
            "manual_updates": 0,
            "last_manual_update_at": None,
        },
    )
    monkeypatch.setattr(gateway_server, "SCHED_EVENT_PROJECTION_ENABLED", False)
    monkeypatch.setattr(gateway_server, "HEARTBEAT_ENABLED", False)
    monkeypatch.setattr(gateway_server, "CRON_ENABLED", False)
    monkeypatch.setattr(gateway_server, "_DEPLOYMENT_PROFILE", "local_workstation")
    monkeypatch.setattr(gateway_server, "_FACTORY_POLICY", build_factory_runtime_policy("HEADQUARTERS"))
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "")
    monkeypatch.setattr(gateway_server, "SESSION_API_TOKEN", "")
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
    monkeypatch.setenv("UA_GATEWAY_PORT", "0")  # Avoid binding real port if it tried
    monkeypatch.setenv("UA_WORK_THREADS_PATH", str((tmp_path / "work_threads.json").resolve()))

    # Bypass the real gateway_server lifespan which initializes the runtime DB and background services.
    # Unit tests only need an OpsService pointed at tmp_path.
    @asynccontextmanager
    async def _test_lifespan(app):
        gateway_server._gateway = InProcessGateway(workspace_base=tmp_path)
        gateway_server._ops_service = OpsService(gateway_server._gateway, tmp_path)
        yield

    monkeypatch.setattr(gateway_server.app.router, "lifespan_context", _test_lifespan)
    
    with TestClient(gateway_server.app) as c:
        yield c

def _create_dummy_session(base_dir: Path, session_id: str, logs: list[str]):
    session_dir = base_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    if logs:
        (session_dir / "run.log").write_text("\n".join(logs), encoding="utf-8")
    return session_dir


def test_gateway_lifespan_runs_stale_reconcile_on_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str((tmp_path / "runtime_state.db").resolve()))
    monkeypatch.setenv("UA_CODER_VP_DB_PATH", str((tmp_path / "coder_vp_state.db").resolve()))
    monkeypatch.setenv("UA_VP_DB_PATH", str((tmp_path / "vp_state.db").resolve()))
    monkeypatch.setenv("UA_GATEWAY_PORT", "0")
    monkeypatch.setenv("UA_DISABLE_HEARTBEAT", "1")
    monkeypatch.setenv("UA_DISABLE_CRON", "1")
    monkeypatch.setattr(gateway_server, "_gateway", None)
    monkeypatch.setattr(gateway_server, "_ops_service", None)
    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_session_runtime", {})
    monkeypatch.setattr(gateway_server, "_vp_event_bridge_task", None)
    monkeypatch.setattr(gateway_server, "_vp_event_bridge_stop_event", None)
    monkeypatch.setattr(gateway_server, "_vp_event_bridge_last_rowid", 0)
    monkeypatch.setattr(gateway_server, "HEARTBEAT_ENABLED", False)
    monkeypatch.setattr(gateway_server, "CRON_ENABLED", False)
    monkeypatch.setattr(gateway_server, "_vp_event_bridge_enabled", False)

    calls: list[int] = []

    def _fake_reconcile() -> int:
        calls.append(1)
        return 0

    monkeypatch.setattr(gateway_server, "_reconcile_stale_vp_missions_on_startup", _fake_reconcile)
    with TestClient(gateway_server.app):
        pass
    assert len(calls) == 1


def test_hooks_readyz_reports_not_initialized(client, monkeypatch):
    monkeypatch.setattr(gateway_server, "_hooks_service", None)
    resp = client.get("/api/v1/hooks/readyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert data["service_initialized"] is False
    assert data["hooks_enabled"] is False


def test_hooks_readyz_reports_initialized_status(client, monkeypatch):
    class _StubHooksService:
        def readiness_status(self):
            return {
                "ready": True,
                "hooks_enabled": True,
                "base_path": "/hooks",
                "max_body_bytes": 1024,
                "mapping_count": 2,
                "mapping_ids": ["composio-youtube-trigger", "youtube-manual-url"],
                "youtube_ingest_mode": "local_worker",
                "youtube_ingest_url_configured": True,
                "youtube_ingest_fail_open": False,
                "hook_default_timeout_seconds": 0,
            }

    monkeypatch.setattr(gateway_server, "_hooks_service", _StubHooksService())
    resp = client.get("/api/v1/hooks/readyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is True
    assert data["service_initialized"] is True
    assert data["hooks_enabled"] is True
    assert data["mapping_count"] == 2

def test_ops_list_sessions(client, tmp_path):
    # Create some dummy sessions on disk
    session_a = _create_dummy_session(tmp_path, "session_A", ["logA"])
    (session_a / "session_checkpoint.json").write_text(
        json.dumps({"original_request": "Do the thing about the widgets"}, indent=2),
        encoding="utf-8",
    )
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

    # Session description should be surfaced when a checkpoint exists.
    row_a = next(s for s in data["sessions"] if s["session_id"] == "session_A")
    assert row_a.get("description") == "Do the thing about the widgets"
    # Session timestamps must be timezone-aware UTC to avoid client-side timezone drift.
    assert str(row_a.get("last_modified", "")).endswith("+00:00")
    assert str(row_a.get("last_activity", "")).endswith("+00:00")

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


def test_dashboard_summary_counts_use_activity_store(client):
    gateway_server._add_notification(
        kind="summary_store_test",
        title="Persisted Summary Event",
        message="Persist me in activity store",
        severity="info",
        requires_action=False,
        metadata={},
    )
    gateway_server._notifications.clear()

    summary = client.get("/api/v1/dashboard/summary")
    assert summary.status_code == 200
    payload = summary.json()
    assert int(payload["notifications"]["total"]) >= 1
    assert int(payload["notifications"]["unread"]) >= 1


def test_dashboard_events_returns_unified_activity_feed(client):
    gateway_server._add_notification(
        kind="csi_insight",
        title="CSI Insight Event",
        message="A full CSI signal payload",
        severity="info",
        requires_action=True,
        metadata={"event_type": "rss_trend_report", "report_key": "rss_trend_report:test"},
    )

    resp = client.get("/api/v1/dashboard/events?limit=20")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["events"]
    first = payload["events"][0]
    assert first["source_domain"] == "csi"
    assert first["kind"] == "csi_insight"
    assert "send_to_simone" in {str(item.get("id")) for item in first.get("actions", [])}


def _first_sse_payload(response) -> dict:
    lines = [line for line in response.text.splitlines() if line.startswith("data:")]
    assert lines
    return json.loads(lines[0][len("data:"):].strip())


def test_dashboard_events_stream_snapshot_and_incremental(client, monkeypatch):
    monkeypatch.setattr(gateway_server, "_dashboard_events_sse_enabled", True)
    first = gateway_server._add_notification(
        kind="csi_insight",
        title="Stream Snapshot",
        message="Snapshot item",
        severity="info",
        requires_action=False,
        metadata={"event_type": "rss_trend_report"},
    )

    snapshot_resp = client.get("/api/v1/dashboard/events/stream?since_seq=0&once=1&limit=20")
    assert snapshot_resp.status_code == 200
    assert snapshot_resp.headers["content-type"].startswith("text/event-stream")
    snapshot_payload = _first_sse_payload(snapshot_resp)
    assert snapshot_payload["kind"] == "snapshot"
    assert any(str(item.get("id") or "") == first["id"] for item in snapshot_payload.get("events", []))
    seq = int(snapshot_payload.get("seq") or 0)

    second = gateway_server._add_notification(
        kind="csi_insight",
        title="Stream Incremental",
        message="Incremental item",
        severity="info",
        requires_action=False,
        metadata={"event_type": "rss_trend_report"},
    )
    event_resp = client.get(f"/api/v1/dashboard/events/stream?since_seq={seq}&once=1&limit=20")
    assert event_resp.status_code == 200
    event_payload = _first_sse_payload(event_resp)
    assert event_payload["kind"] == "event"
    assert str(event_payload.get("op") or "") == "upsert"
    assert str((event_payload.get("event") or {}).get("id") or "") == second["id"]


def test_dashboard_events_counters_endpoint(client):
    first = gateway_server._add_notification(
        kind="csi_insight",
        title="Counter CSI",
        message="Actionable counter",
        severity="info",
        requires_action=True,
        metadata={"event_type": "rss_insight_daily"},
    )
    second = gateway_server._add_notification(
        kind="tutorial_review_ready",
        title="Counter Tutorial",
        message="Tutorial counter",
        severity="info",
        requires_action=False,
        metadata={},
    )

    read_resp = client.post(
        f"/api/v1/dashboard/activity/{second['id']}/action",
        json={"action": "mark_read"},
    )
    assert read_resp.status_code == 200

    counters = client.get("/api/v1/dashboard/events/counters?since=2000-01-01T00:00:00Z")
    assert counters.status_code == 200
    payload = counters.json()
    assert int(payload["totals"]["total"]) >= 2
    assert int(payload["totals"]["actionable"]) >= 1
    assert int(payload["by_source"]["csi"]["total"]) >= 1
    assert int(payload["by_source"]["tutorial"]["total"]) >= 1
    assert str(first["id"])


def test_dashboard_events_presets_owner_scoping(client):
    created = client.post(
        "/api/v1/dashboard/events/presets",
        json={
            "name": "CSI Actionable",
            "filters": {"source_domain": "csi", "actionable_only": True, "time_window": "24h"},
            "is_default": True,
        },
        headers={"x-ua-dashboard-owner": "owner_a"},
    )
    assert created.status_code == 200
    preset = created.json()["preset"]

    list_a = client.get(
        "/api/v1/dashboard/events/presets",
        headers={"x-ua-dashboard-owner": "owner_a"},
    )
    assert list_a.status_code == 200
    assert any(str(item.get("id") or "") == preset["id"] for item in list_a.json()["presets"])

    list_b = client.get(
        "/api/v1/dashboard/events/presets",
        headers={"x-ua-dashboard-owner": "owner_b"},
    )
    assert list_b.status_code == 200
    assert all(str(item.get("id") or "") != preset["id"] for item in list_b.json()["presets"])

    blocked = client.patch(
        f"/api/v1/dashboard/events/presets/{preset['id']}",
        json={"name": "Blocked rename"},
        headers={"x-ua-dashboard-owner": "owner_b"},
    )
    assert blocked.status_code == 404

    deleted = client.delete(
        f"/api/v1/dashboard/events/presets/{preset['id']}",
        headers={"x-ua-dashboard-owner": "owner_a"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


def test_dashboard_digest_compaction_for_routine_notifications(client, monkeypatch):
    monkeypatch.setattr(gateway_server, "_activity_digest_enabled", True)
    before = len(gateway_server._notifications)
    first = gateway_server._add_notification(
        kind="cron_job_finished",
        title="Cron Finished",
        message="Routine completion 1",
        severity="info",
        requires_action=False,
        metadata={"job_id": "job_1"},
    )
    second = gateway_server._add_notification(
        kind="cron_job_finished",
        title="Cron Finished",
        message="Routine completion 2",
        severity="info",
        requires_action=False,
        metadata={"job_id": "job_2"},
    )
    after = len(gateway_server._notifications)
    assert after == before + 1
    assert first["id"] == second["id"]
    metadata = second.get("metadata") if isinstance(second.get("metadata"), dict) else {}
    assert bool(metadata.get("digest_compacted")) is True
    assert int(metadata.get("digest_event_count") or 0) >= 2


def test_ops_activity_events_metrics_endpoint(client, monkeypatch):
    monkeypatch.setattr(gateway_server, "_dashboard_events_sse_enabled", True)
    monkeypatch.setattr(gateway_server, "_activity_digest_enabled", True)
    gateway_server._add_notification(
        kind="cron_job_finished",
        title="Metrics Digest 1",
        message="metrics sample one",
        severity="info",
        requires_action=False,
        metadata={"job_id": "metrics_1"},
    )
    gateway_server._add_notification(
        kind="cron_job_finished",
        title="Metrics Digest 2",
        message="metrics sample two",
        severity="info",
        requires_action=False,
        metadata={"job_id": "metrics_2"},
    )
    stream_resp = client.get("/api/v1/dashboard/events/stream?since_seq=0&once=1&limit=20")
    assert stream_resp.status_code == 200

    metrics_resp = client.get("/api/v1/ops/metrics/activity-events")
    assert metrics_resp.status_code == 200
    metrics = metrics_resp.json().get("metrics", {})
    counters = metrics.get("counters", {})
    assert int(counters.get("events_sse_connects", 0) or 0) >= 1
    assert int(counters.get("events_sse_disconnects", 0) or 0) >= 1
    assert int(counters.get("events_sse_payloads", 0) or 0) >= 1
    assert int(counters.get("digest_compacted_total", 0) or 0) >= 2
    assert "digest_buckets_open" in counters


def test_dashboard_events_cursor_pagination(client):
    created_ids: list[str] = []
    for idx in range(5):
        record = gateway_server._add_notification(
            kind="cursor_test",
            title=f"Cursor Event {idx}",
            message=f"Cursor payload {idx}",
            severity="info",
            requires_action=False,
            metadata={"cursor_index": idx},
        )
        created_ids.append(str(record["id"]))

    collected_ids: list[str] = []
    cursor: str | None = None
    has_more = True
    for _ in range(10):
        if not has_more:
            break
        query = "/api/v1/dashboard/events?limit=2&kind=cursor_test&since=2000-01-01T00:00:00Z"
        if cursor:
            query += f"&cursor={cursor}"
        resp = client.get(query)
        assert resp.status_code == 200
        payload = resp.json()
        rows = payload.get("events", [])
        assert len(rows) <= 2
        collected_ids.extend(str(row.get("id") or "") for row in rows)
        cursor = payload.get("next_cursor")
        has_more = bool(payload.get("has_more"))
        if has_more:
            assert isinstance(cursor, str) and cursor

    assert set(created_ids).issubset(set(collected_ids))


def test_dashboard_activity_send_to_simone_dispatches_hook_action(client, monkeypatch):
    class _HookDispatchStub:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def dispatch_internal_action(self, action_payload: dict):
            self.calls.append(action_payload)
            return True, "agent"

    hook_stub = _HookDispatchStub()
    monkeypatch.setattr(gateway_server, "_hooks_service", hook_stub)
    record = gateway_server._add_notification(
        kind="csi_insight",
        title="CSI Insight Ready",
        message="Insight content for manual handoff",
        severity="info",
        requires_action=True,
        metadata={"event_type": "rss_insight_daily"},
    )

    resp = client.post(
        f"/api/v1/dashboard/activity/{record['id']}/send-to-simone",
        json={"instruction": "Please investigate and propose actions."},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert hook_stub.calls
    assert "Please investigate and propose actions." in str(hook_stub.calls[0].get("message") or "")
    kinds = [str(item.get("kind") or "") for item in gateway_server._notifications]
    assert "simone_handoff_requested" in kinds
    assert "simone_handoff_completed" in kinds


def test_dashboard_activity_actions_are_audited(client, monkeypatch):
    class _HookDispatchStub:
        async def dispatch_internal_action(self, action_payload: dict):
            return True, "agent"

    monkeypatch.setattr(gateway_server, "_hooks_service", _HookDispatchStub())
    record = gateway_server._add_notification(
        kind="csi_insight",
        title="Audit Me",
        message="Audit trail payload",
        severity="info",
        requires_action=True,
        metadata={"event_type": "rss_trend_report"},
    )

    mark_read = client.post(
        f"/api/v1/dashboard/activity/{record['id']}/action",
        json={"action": "mark_read", "note": "handled"},
        headers={"x-user-id": "ops_owner"},
    )
    assert mark_read.status_code == 200

    handoff = client.post(
        f"/api/v1/dashboard/activity/{record['id']}/send-to-simone",
        json={"instruction": "Investigate trend implications."},
        headers={"x-user-id": "ops_owner"},
    )
    assert handoff.status_code == 200
    assert handoff.json().get("ok") is True

    audit_resp = client.get(f"/api/v1/dashboard/activity/{record['id']}/audit?limit=20")
    assert audit_resp.status_code == 200
    audit_rows = audit_resp.json().get("audit", [])
    assert any(
        str(row.get("action") or "") == "mark_read"
        and str(row.get("actor") or "") == "ops_owner"
        and str(row.get("outcome") or "") == "ok"
        for row in audit_rows
    )
    assert any(
        str(row.get("action") or "") == "send_to_simone"
        and str(row.get("actor") or "") == "ops_owner"
        and str(row.get("outcome") or "") == "completed"
        for row in audit_rows
    )


def test_dashboard_activity_action_updates_notification_status_and_pin(client):
    record = gateway_server._add_notification(
        kind="csi_insight",
        title="CSI Insight Actionable",
        message="Action me",
        severity="info",
        requires_action=True,
        metadata={"event_type": "rss_trend_report"},
    )

    mark_read = client.post(
        f"/api/v1/dashboard/activity/{record['id']}/action",
        json={"action": "mark_read"},
    )
    assert mark_read.status_code == 200
    mark_payload = mark_read.json()
    assert mark_payload["ok"] is True
    assert mark_payload["event"]["status"] == "read"

    pin = client.post(
        f"/api/v1/dashboard/activity/{record['id']}/action",
        json={"action": "pin"},
    )
    assert pin.status_code == 200
    pin_payload = pin.json()
    assert pin_payload["ok"] is True
    assert bool(pin_payload["event"]["metadata"].get("pinned")) is True

    pinned_feed = client.get("/api/v1/dashboard/events?limit=20&pinned=true")
    assert pinned_feed.status_code == 200
    rows = pinned_feed.json().get("events", [])
    assert any(str(item.get("id") or "") == record["id"] for item in rows)


def test_dashboard_notification_bulk_and_purge_use_activity_store(client):
    record = gateway_server._add_notification(
        kind="csi_insight",
        title="CSI Persisted",
        message="Persisted into activity DB",
        severity="info",
        requires_action=False,
        metadata={"event_type": "rss_trend_report"},
    )

    gateway_server._notifications.clear()

    bulk = client.post(
        "/api/v1/dashboard/notifications/bulk",
        json={"status": "read", "kind": "csi_insight", "limit": 50},
    )
    assert bulk.status_code == 200
    bulk_payload = bulk.json()
    assert bulk_payload["updated"] >= 1

    after_bulk = client.get("/api/v1/dashboard/events?kind=csi_insight&status=read&limit=20")
    assert after_bulk.status_code == 200
    read_rows = after_bulk.json().get("events", [])
    assert any(str(item.get("id") or "") == record["id"] for item in read_rows)

    purge = client.post(
        "/api/v1/dashboard/notifications/purge",
        json={"kind": "csi_insight", "current_status": "read"},
    )
    assert purge.status_code == 200
    assert purge.json()["deleted"] >= 1

    after_purge = client.get("/api/v1/dashboard/events?kind=csi_insight&limit=20")
    assert after_purge.status_code == 200
    remaining_rows = after_purge.json().get("events", [])
    assert all(str(item.get("id") or "") != record["id"] for item in remaining_rows)


def test_dashboard_csi_reports_fallbacks_to_notifications(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_DB_PATH", str((tmp_path / "missing_csi.db").resolve()))

    from universal_agent import gateway_server

    gateway_server._notifications.append(  # type: ignore[attr-defined]
        {
            "id": "ntf_csi_1",
            "kind": "csi_insight",
            "title": "CSI Insight: rss_trend_report",
            "message": "CSI analytics signal received.",
            "session_id": "session_hook_csi_csi_analytics_rss_trend_report",
            "severity": "info",
            "requires_action": True,
            "status": "new",
            "created_at": "2026-02-07T10:00:00+00:00",
            "updated_at": "2026-02-07T10:00:00+00:00",
            "channels": ["dashboard"],
            "email_targets": [],
            "metadata": {"event_type": "rss_trend_report", "event_id": "evt-1"},
        }
    )

    resp = client.get("/api/v1/dashboard/csi/reports?limit=5")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["source"] == "notification_fallback"
    assert len(payload["reports"]) >= 1
    assert payload["reports"][0]["report_type"] == "rss_trend_report"


def test_dashboard_csi_reports_fallbacks_to_sessions_when_notifications_empty(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_DB_PATH", str((tmp_path / "missing_csi.db").resolve()))
    _create_dummy_session(
        tmp_path,
        "session_hook_csi_csi_analytics_rss_insight_emerging",
        ["[00:00:00] INFO CSI trend run"],
    )

    resp = client.get("/api/v1/dashboard/csi/reports?limit=5")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["source"] == "session_fallback"
    assert len(payload["reports"]) >= 1
    assert payload["reports"][0]["session_id"].startswith("session_hook_csi_")


def test_dashboard_csi_reports_include_specialist_synthesis_notifications(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_DB_PATH", str((tmp_path / "missing_csi.db").resolve()))
    gateway_server._add_notification(
        kind="csi_specialist_hourly_synthesis",
        title="CSI Specialist Hourly Synthesis",
        message="Hourly synthesis content",
        severity="info",
        requires_action=False,
        metadata={
            "report_key": "csi_specialist_hourly_synthesis:2026-02-27T14:00:00Z",
            "report_class": "specialist_hourly",
            "window_hours": 1,
            "source_mix": {"rss": 3, "reddit": 1},
        },
    )

    resp = client.get("/api/v1/dashboard/csi/reports?limit=20")
    assert resp.status_code == 200
    payload = resp.json()
    reports = payload.get("reports") or []
    assert any(str(item.get("report_class") or "") == "specialist_hourly" for item in reports)


def test_dashboard_csi_health_includes_overnight_and_source_health(client, tmp_path, monkeypatch):
    db_path = tmp_path / "csi_health.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                source TEXT,
                event_type TEXT,
                occurred_at TEXT,
                delivered INTEGER DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE dead_letter (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE delivery_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                target TEXT,
                delivered INTEGER,
                status_code INTEGER,
                error_class TEXT,
                error_detail TEXT,
                attempted_at TEXT
            )
            """
        )
        now = datetime.now(timezone.utc)
        recent = now.isoformat()
        conn.execute(
            "INSERT INTO events (event_id, source, event_type, occurred_at, delivered) VALUES (?, ?, ?, ?, ?)",
            ("evt-1", "csi_analytics", "rss_trend_report", recent, 1),
        )
        conn.execute(
            "INSERT INTO events (event_id, source, event_type, occurred_at, delivered) VALUES (?, ?, ?, ?, ?)",
            ("evt-2", "reddit_discovery", "reddit_trend_report", recent, 1),
        )
        conn.execute(
            """
            INSERT INTO delivery_attempts (
                event_id, target, delivered, status_code, attempted_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("evt-1", "ua_signals_ingest", 1, 200, recent),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setenv("CSI_DB_PATH", str(db_path))
    resp = client.get("/api/v1/dashboard/csi/health")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert isinstance(payload.get("source_health"), list)
    assert isinstance((payload.get("overnight_continuity") or {}).get("checks"), list)
    assert int(payload.get("delivery_attempts_last_24h") or 0) >= 1
    assert isinstance(payload.get("delivery_targets"), list)


def test_dashboard_csi_reports_quality_gate_suppresses_near_duplicate_emerging(client, tmp_path, monkeypatch):
    db_path = tmp_path / "csi_quality.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE insight_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_key TEXT UNIQUE NOT NULL,
                report_type TEXT NOT NULL,
                window_start_utc TEXT NOT NULL,
                window_end_utc TEXT NOT NULL,
                model_name TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                report_markdown TEXT NOT NULL,
                report_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE trend_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_key TEXT UNIQUE NOT NULL,
                window_start_utc TEXT NOT NULL,
                window_end_utc TEXT NOT NULL,
                model_name TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                report_markdown TEXT NOT NULL,
                report_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                event_type TEXT,
                occurred_at TEXT,
                subject_json TEXT
            );
            """
        )
        now = datetime.now(timezone.utc)
        daily_json = json.dumps({"top_themes": [{"theme": "agentic ai"}, {"theme": "autonomous agents"}]})
        emerging_json = json.dumps({"top_themes": [{"theme": "agentic ai"}, {"theme": "autonomous agents"}]})
        conn.execute(
            """
            INSERT INTO insight_reports (
                report_key, report_type, window_start_utc, window_end_utc, model_name,
                report_markdown, report_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "daily:test",
                "daily",
                (now - timedelta(hours=24)).isoformat(),
                now.isoformat(),
                "claude",
                "daily report content",
                daily_json,
                now.isoformat(),
            ),
        )
        conn.execute(
            """
            INSERT INTO insight_reports (
                report_key, report_type, window_start_utc, window_end_utc, model_name,
                report_markdown, report_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "emerging:test",
                "emerging",
                (now - timedelta(hours=6)).isoformat(),
                now.isoformat(),
                "claude",
                "emerging report content",
                emerging_json,
                now.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setenv("CSI_DB_PATH", str(db_path))
    default_resp = client.get("/api/v1/dashboard/csi/reports?limit=10")
    assert default_resp.status_code == 200
    default_reports = default_resp.json().get("reports") or []
    assert not any(str(item.get("report_class") or "") == "emerging" for item in default_reports)
    assert any(
        str((item.get("quality_gate") or {}).get("status") or "") == "near_duplicate"
        for item in default_reports
    )

    include_resp = client.get("/api/v1/dashboard/csi/reports?limit=10&include_suppressed=true")
    assert include_resp.status_code == 200
    include_reports = include_resp.json().get("reports") or []
    emerging = next((item for item in include_reports if str(item.get("report_class") or "") == "emerging"), None)
    assert emerging is not None
    assert bool(emerging.get("suppressed")) is True


def test_dashboard_tutorial_runs_lists_flat_and_dated_layouts(client, tmp_path, monkeypatch):
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", artifacts_root)
    tutorial_root = artifacts_root / "youtube-tutorial-creation"

    dated_run = tutorial_root / "2026-02-27" / "compounding-units-of-work__102742"
    dated_run.mkdir(parents=True, exist_ok=True)
    (dated_run / "manifest.json").write_text(
        json.dumps(
            {
                "video_id": "5LgRgqimy80",
                "title": "How to Build Compounding Units of Work",
                "status": "full",
            }
        ),
        encoding="utf-8",
    )
    (dated_run / "README.md").write_text("# dated\n", encoding="utf-8")

    flat_run = tutorial_root / "NAWKFRaR0Sk__claude-code-tasks"
    flat_run.mkdir(parents=True, exist_ok=True)
    (flat_run / "manifest.json").write_text(
        json.dumps(
            {
                "video_id": "NAWKFRaR0Sk",
                "title": "Claude Code Tasks: Stop Babysitting Your AI Agent",
                "status": "full",
            }
        ),
        encoding="utf-8",
    )
    (flat_run / "README.md").write_text("# flat\n", encoding="utf-8")

    resp = client.get("/api/v1/dashboard/tutorials/runs?limit=20")
    assert resp.status_code == 200
    payload = resp.json()
    runs = payload["runs"]
    run_paths = {row["run_path"] for row in runs}
    video_ids = {row["video_id"] for row in runs}

    assert "youtube-tutorial-creation/2026-02-27/compounding-units-of-work__102742" in run_paths
    assert "youtube-tutorial-creation/NAWKFRaR0Sk__claude-code-tasks" in run_paths
    assert "5LgRgqimy80" in video_ids
    assert "NAWKFRaR0Sk" in video_ids


def test_ops_purge_csi_sessions_dry_run_and_delete(client, tmp_path):
    _create_dummy_session(tmp_path, "session_hook_csi_alpha", ["alpha"])
    _create_dummy_session(tmp_path, "session_hook_csi_bravo", ["bravo"])
    _create_dummy_session(tmp_path, "session_hook_csi_charlie", ["charlie"])

    dry_run = client.post(
        "/api/v1/ops/sessions/csi/purge",
        json={
            "dry_run": True,
            "keep_latest": 1,
            "older_than_minutes": 0,
            "include_active": False,
        },
    )
    assert dry_run.status_code == 200
    dry_payload = dry_run.json()
    assert dry_payload["status"] == "dry_run"
    assert dry_payload["total_csi_sessions"] == 3
    assert len(dry_payload["candidates"]) == 2
    assert len(dry_payload["skipped"]["protected"]) == 1

    purge = client.post(
        "/api/v1/ops/sessions/csi/purge",
        json={
            "dry_run": False,
            "keep_latest": 1,
            "older_than_minutes": 0,
            "include_active": False,
        },
    )
    assert purge.status_code == 200
    payload = purge.json()
    assert payload["status"] == "ok"
    assert len(payload["deleted"]) == 2
    assert len(payload["skipped"]["protected"]) == 1

    remaining = [p.name for p in tmp_path.iterdir() if p.is_dir() and p.name.startswith("session_hook_csi_")]
    assert len(remaining) == 1


def test_dashboard_tutorial_bootstrap_repo_runs_create_script(client, tmp_path, monkeypatch):
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", artifacts_root)

    run_dir = artifacts_root / "youtube-tutorial-creation" / "demo-run"
    impl_dir = run_dir / "implementation"
    impl_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "title": "Demo Run",
                "video_id": "demo123",
                "status": "full",
            }
        ),
        encoding="utf-8",
    )
    script = impl_dir / "create_new_repo.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "TARGET_ROOT=\"${1:?}\"\n"
        "REPO_NAME=\"${2:?}\"\n"
        "mkdir -p \"$TARGET_ROOT/$REPO_NAME\"\n"
        "echo \"Repo ready: $TARGET_ROOT/$REPO_NAME\"\n",
        encoding="utf-8",
    )

    target_root = tmp_path / "generated_repos"
    resp = client.post(
        "/api/v1/dashboard/tutorials/bootstrap-repo",
        json={
            "run_path": "youtube-tutorial-creation/demo-run",
            "target_root": str(target_root),
            "repo_name": "demo_repo",
            "timeout_seconds": 120,
            "execution_target": "server",
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["execution_target"] == "server"
    assert payload["repo_name"] == "demo_repo"
    assert "Repo ready:" in str(payload.get("stdout") or "")
    assert (target_root / "demo_repo").is_dir()


def test_dashboard_tutorial_bootstrap_repo_local_queue_and_worker_flow(client, tmp_path, monkeypatch):
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", artifacts_root)
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "ops_test_token")

    run_dir = artifacts_root / "youtube-tutorial-creation" / "demo-run"
    impl_dir = run_dir / "implementation"
    impl_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "title": "Demo Run",
                "video_id": "demo123",
                "status": "full",
            }
        ),
        encoding="utf-8",
    )
    (impl_dir / "create_new_repo.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "TARGET_ROOT=\"${1:?}\"\n"
        "REPO_NAME=\"${2:?}\"\n"
        "mkdir -p \"$TARGET_ROOT/$REPO_NAME\"\n"
        "echo \"Repo ready: $TARGET_ROOT/$REPO_NAME\"\n",
        encoding="utf-8",
    )
    (impl_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")

    create_resp = client.post(
        "/api/v1/dashboard/tutorials/bootstrap-repo",
        json={
            "run_path": "youtube-tutorial-creation/demo-run",
            "target_root": "/home/kjdragan/YoutubeCodeExamples",
            "repo_name": "demo_repo",
            "execution_target": "local",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["queued"] is True
    assert created["execution_target"] == "local"
    assert created["existing_job_reused"] is False
    job_id = str(created["job_id"])

    listed = client.get("/api/v1/dashboard/tutorials/bootstrap-jobs?limit=20").json()["jobs"]
    selected = next(item for item in listed if str(item.get("job_id")) == job_id)
    assert selected["status"] == "queued"
    assert selected["repo_name"] == "demo_repo"

    headers = {"x-ua-ops-token": "ops_test_token"}
    claim_resp = client.post(
        "/api/v1/ops/tutorials/bootstrap-jobs/claim",
        headers=headers,
        json={"worker_id": "local-desktop-worker"},
    )
    assert claim_resp.status_code == 200
    claim_payload = claim_resp.json()
    job = claim_payload["job"]
    assert job["job_id"] == job_id
    assert job["status"] == "running"
    assert job["worker_id"] == "local-desktop-worker"

    bundle_resp = client.get(
        f"/api/v1/ops/tutorials/bootstrap-jobs/{job_id}/bundle",
        headers=headers,
    )
    assert bundle_resp.status_code == 200
    tar_data = io.BytesIO(bundle_resp.content)
    with tarfile.open(fileobj=tar_data, mode="r:gz") as archive:
        names = set(archive.getnames())
    assert "implementation/create_new_repo.sh" in names
    assert "implementation/requirements.txt" in names

    result_resp = client.post(
        f"/api/v1/ops/tutorials/bootstrap-jobs/{job_id}/result",
        headers=headers,
        json={
            "worker_id": "local-desktop-worker",
            "status": "completed",
            "repo_dir": "/home/kjdragan/YoutubeCodeExamples/demo_repo",
            "stdout": "Repo ready: /home/kjdragan/YoutubeCodeExamples/demo_repo",
            "stderr": "",
        },
    )
    assert result_resp.status_code == 200
    result_job = result_resp.json()["job"]
    assert result_job["status"] == "completed"
    assert result_job["repo_dir"] == "/home/kjdragan/YoutubeCodeExamples/demo_repo"
    assert str(result_job.get("repo_open_uri") or "").startswith("file://")
    assert "repo_open_hint" in result_job

    latest = client.get(f"/api/v1/dashboard/tutorials/bootstrap-jobs?run_path=youtube-tutorial-creation/demo-run").json()["jobs"]
    assert len(latest) == 1
    assert latest[0]["job_id"] == job_id
    assert latest[0]["status"] == "completed"
    assert str(latest[0].get("repo_open_uri") or "").startswith("file://")


def test_dashboard_tutorial_bootstrap_repo_local_queue_reuses_existing_active_job(client, tmp_path, monkeypatch):
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", artifacts_root)

    run_dir = artifacts_root / "youtube-tutorial-creation" / "reuse-run"
    impl_dir = run_dir / "implementation"
    impl_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"title": "Reuse Run", "video_id": "reuse123", "status": "full"}),
        encoding="utf-8",
    )
    (impl_dir / "create_new_repo.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "TARGET_ROOT=\"${1:?}\"\n"
        "REPO_NAME=\"${2:?}\"\n"
        "mkdir -p \"$TARGET_ROOT/$REPO_NAME\"\n"
        "echo \"Repo ready: $TARGET_ROOT/$REPO_NAME\"\n",
        encoding="utf-8",
    )

    first = client.post(
        "/api/v1/dashboard/tutorials/bootstrap-repo",
        json={
            "run_path": "youtube-tutorial-creation/reuse-run",
            "repo_name": "reuse_repo_a",
            "execution_target": "local",
        },
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["queued"] is True
    assert first_payload["existing_job_reused"] is False
    first_job_id = str(first_payload["job_id"])

    second = client.post(
        "/api/v1/dashboard/tutorials/bootstrap-repo",
        json={
            "run_path": "youtube-tutorial-creation/reuse-run",
            "repo_name": "reuse_repo_b",
            "execution_target": "local",
        },
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["queued"] is True
    assert second_payload["existing_job_reused"] is True
    assert str(second_payload["job_id"]) == first_job_id
    assert str(second_payload["status"]).lower() in {"queued", "running"}

    listed = client.get("/api/v1/dashboard/tutorials/bootstrap-jobs?run_path=youtube-tutorial-creation/reuse-run").json()["jobs"]
    assert len(listed) == 1
    assert str(listed[0]["job_id"]) == first_job_id


def test_dashboard_tutorial_bootstrap_repo_failed_result_preserves_error_metadata(client, tmp_path, monkeypatch):
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", artifacts_root)
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "ops_test_token")

    run_dir = artifacts_root / "youtube-tutorial-creation" / "failed-run"
    impl_dir = run_dir / "implementation"
    impl_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"title": "Failed Run", "video_id": "failed123", "status": "full"}),
        encoding="utf-8",
    )
    (impl_dir / "create_new_repo.sh").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")

    create_resp = client.post(
        "/api/v1/dashboard/tutorials/bootstrap-repo",
        json={"run_path": "youtube-tutorial-creation/failed-run", "execution_target": "local"},
    )
    assert create_resp.status_code == 200
    job_id = str(create_resp.json()["job_id"])

    headers = {"x-ua-ops-token": "ops_test_token"}
    claim_resp = client.post(
        "/api/v1/ops/tutorials/bootstrap-jobs/claim",
        headers=headers,
        json={"worker_id": "local-desktop-worker"},
    )
    assert claim_resp.status_code == 200
    assert str(claim_resp.json()["job"]["job_id"]) == job_id

    failure_text = "Bootstrap script exited with code 1"
    result_resp = client.post(
        f"/api/v1/ops/tutorials/bootstrap-jobs/{job_id}/result",
        headers=headers,
        json={
            "worker_id": "local-desktop-worker",
            "status": "failed",
            "repo_dir": "/home/kjdragan/YoutubeCodeExamples/failed_repo",
            "stderr": "traceback...",
            "error": failure_text,
        },
    )
    assert result_resp.status_code == 200
    result_job = result_resp.json()["job"]
    assert result_job["status"] == "failed"
    assert result_job["error"] == failure_text
    assert result_job["repo_dir"] == "/home/kjdragan/YoutubeCodeExamples/failed_repo"

    notif_resp = client.get("/api/v1/dashboard/tutorials/notifications?limit=20")
    assert notif_resp.status_code == 200
    notifications = notif_resp.json()["notifications"]
    failure_events = [n for n in notifications if str(n.get("kind")) == "tutorial_repo_bootstrap_failed"]
    assert failure_events, "Expected tutorial_repo_bootstrap_failed notification"
    latest = failure_events[0]
    metadata = latest.get("metadata") or {}
    assert str(metadata.get("job_id") or "") == job_id
    assert str(metadata.get("error") or "") == failure_text


def test_dashboard_tutorial_notifications_hide_dismissed_by_default(client):
    notif = gateway_server._add_notification(
        kind="tutorial_repo_bootstrap_queued",
        title="Tutorial Repo Bootstrap Queued",
        message="Queued local repo creation for: test-run",
        severity="info",
        requires_action=False,
        metadata={"tutorial_run_path": "youtube-tutorial-creation/test-run"},
    )
    assert isinstance(notif, dict)
    notif["status"] = "dismissed"

    default_resp = client.get("/api/v1/dashboard/tutorials/notifications?limit=50")
    assert default_resp.status_code == 200
    default_ids = {str(item.get("id") or "") for item in default_resp.json().get("notifications") or []}
    assert str(notif.get("id") or "") not in default_ids

    include_resp = client.get("/api/v1/dashboard/tutorials/notifications?limit=50&include_dismissed=true")
    assert include_resp.status_code == 200
    include_ids = {str(item.get("id") or "") for item in include_resp.json().get("notifications") or []}
    assert str(notif.get("id") or "") in include_ids


def test_dashboard_tutorial_bootstrap_repo_local_redis_dispatch_publishes_mission(client, tmp_path, monkeypatch):
    class _FakeMissionBus:
        def __init__(self):
            self.published = []

        def publish_mission(self, envelope):
            self.published.append(envelope)
            return f"{len(self.published)}-0"

    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", artifacts_root)
    fake_bus = _FakeMissionBus()
    monkeypatch.setattr(gateway_server, "_delegation_mission_bus", fake_bus)

    run_dir = artifacts_root / "youtube-tutorial-creation" / "redis-run"
    impl_dir = run_dir / "implementation"
    impl_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"title": "Redis Run", "video_id": "redis123", "status": "full"}),
        encoding="utf-8",
    )
    (impl_dir / "create_new_repo.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "TARGET_ROOT=\"${1:?}\"\n"
        "REPO_NAME=\"${2:?}\"\n"
        "mkdir -p \"$TARGET_ROOT/$REPO_NAME\"\n"
        "echo \"Repo ready: $TARGET_ROOT/$REPO_NAME\"\n",
        encoding="utf-8",
    )

    create_resp = client.post(
        "/api/v1/dashboard/tutorials/bootstrap-repo",
        json={
            "run_path": "youtube-tutorial-creation/redis-run",
            "target_root": "/home/kjdragan/YoutubeCodeExamples",
            "repo_name": "redis_repo",
            "execution_target": "local",
        },
    )
    assert create_resp.status_code == 200
    payload = create_resp.json()
    assert payload["queued"] is True
    assert payload["dispatch_backend"] == "redis_stream"
    assert len(fake_bus.published) == 1
    assert str(fake_bus.published[0].job_id) == str(payload["job_id"])

    listed = client.get("/api/v1/dashboard/tutorials/bootstrap-jobs?run_path=youtube-tutorial-creation/redis-run").json()["jobs"]
    assert len(listed) == 1
    assert listed[0]["dispatch_backend"] == "redis_stream"
    assert str(listed[0].get("delegation_message_id") or "").endswith("-0")
    job_id = str(payload["job_id"])

    start_resp = client.post(
        f"/api/v1/ops/tutorials/bootstrap-jobs/{job_id}/start",
        json={"worker_id": "worker_factory-local-1"},
    )
    assert start_resp.status_code == 200
    assert start_resp.json()["job"]["status"] == "running"

    claim_resp = client.post(
        "/api/v1/ops/tutorials/bootstrap-jobs/claim",
        json={"worker_id": "local-desktop-worker"},
    )
    assert claim_resp.status_code == 200
    assert claim_resp.json()["job"] is None


def test_factory_capabilities_and_registration_endpoints(client):
    caps_resp = client.get("/api/v1/factory/capabilities")
    assert caps_resp.status_code == 200
    caps_payload = caps_resp.json()
    assert "factory" in caps_payload
    assert str(caps_payload["factory"].get("factory_id") or "")

    reg_resp = client.post(
        "/api/v1/factory/registrations",
        json={
            "factory_id": "factory-local-1",
            "factory_role": "LOCAL_WORKER",
            "registration_status": "online",
            "heartbeat_latency_ms": 42.5,
            "capabilities": ["tutorial_bootstrap_consumer", "transport:redis"],
            "metadata": {"worker_id": "worker_factory-local-1"},
        },
    )
    assert reg_resp.status_code == 200
    assert reg_resp.json()["ok"] is True
    registration = reg_resp.json()["registration"]
    assert registration["factory_id"] == "factory-local-1"
    assert registration["factory_role"] == "LOCAL_WORKER"

    list_resp = client.get("/api/v1/factory/registrations")
    assert list_resp.status_code == 200
    list_payload = list_resp.json()
    assert list_payload["count"] >= 1
    assert any(str(row.get("factory_id") or "") == "factory-local-1" for row in list_payload["registrations"])


def test_dashboard_coder_vp_metrics_endpoint(client, tmp_path):
    gateway = gateway_server.get_gateway()
    conn = gateway.get_coder_vp_db_conn()
    assert conn is not None

    vp_id = f"vp.coder.dashboard.{time.time_ns()}"
    now = datetime.now(timezone.utc)
    started = (now - timedelta(seconds=10)).isoformat()
    completed = (now - timedelta(seconds=3)).isoformat()

    upsert_vp_session(
        conn,
        vp_id=vp_id,
        runtime_id="runtime.coder_vp.inprocess",
        status="active",
        session_id=f"{vp_id}.session",
        workspace_dir=str((tmp_path / vp_id.replace('.', '_')).resolve()),
        metadata={"lane": "coder_vp"},
    )
    mission_id = f"{vp_id}.mission"
    upsert_vp_mission(
        conn,
        mission_id=mission_id,
        vp_id=vp_id,
        status="completed",
        objective="dashboard aggregation",
        run_id="run-dashboard",
        started_at=started,
        completed_at=completed,
    )
    append_vp_event(
        conn,
        event_id=f"{mission_id}.dispatch",
        mission_id=mission_id,
        vp_id=vp_id,
        event_type="vp.mission.dispatched",
        payload={"run_id": "run-dashboard"},
    )
    append_vp_event(
        conn,
        event_id=f"{mission_id}.completed",
        mission_id=mission_id,
        vp_id=vp_id,
        event_type="vp.mission.completed",
        payload={"trace_id": "trace-dashboard"},
    )

    resp = client.get(f"/api/v1/dashboard/metrics/coder-vp?vp_id={vp_id}&mission_limit=10&event_limit=10")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["metrics"]["vp_id"] == vp_id
    assert payload["metrics"]["mission_counts"]["completed"] == 1
    assert payload["metrics"]["event_counts"]["vp.mission.completed"] == 1


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


def test_dashboard_notification_purge(client):
    from universal_agent import gateway_server

    gateway_server._notifications.extend(  # type: ignore[attr-defined]
        [
            {
                "id": "ntf_purge_1",
                "kind": "system_error",
                "title": "Old Error",
                "message": "old",
                "session_id": None,
                "severity": "error",
                "requires_action": False,
                "status": "new",
                "created_at": "2026-02-07T00:00:00+00:00",
                "updated_at": "2026-02-07T00:00:00+00:00",
                "channels": ["dashboard"],
                "email_targets": [],
                "metadata": {},
            },
            {
                "id": "ntf_purge_2",
                "kind": "csi_insight",
                "title": "Recent",
                "message": "recent",
                "session_id": None,
                "severity": "info",
                "requires_action": False,
                "status": "new",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "channels": ["dashboard"],
                "email_targets": [],
                "metadata": {},
            },
        ]
    )

    missing_filter_resp = client.post("/api/v1/dashboard/notifications/purge", json={})
    assert missing_filter_resp.status_code == 400

    purge_resp = client.post(
        "/api/v1/dashboard/notifications/purge",
        json={"kind": "system_error", "older_than_hours": 24},
    )
    assert purge_resp.status_code == 200
    payload = purge_resp.json()
    assert payload["deleted"] == 1
    assert payload["remaining"] >= 1
    remaining_ids = {item["id"] for item in gateway_server._notifications}
    assert "ntf_purge_1" not in remaining_ids
    assert "ntf_purge_2" in remaining_ids

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


def test_ops_coder_vp_metrics_endpoint(client, tmp_path):
    gateway = gateway_server.get_gateway()
    conn = gateway.get_coder_vp_db_conn()
    assert conn is not None

    vp_id = f"vp.coder.test.{time.time_ns()}"
    now = datetime.now(timezone.utc)
    started_a = (now - timedelta(seconds=12)).isoformat()
    completed_a = (now - timedelta(seconds=2)).isoformat()
    started_b = (now - timedelta(seconds=9)).isoformat()
    completed_b = (now - timedelta(seconds=1)).isoformat()

    upsert_vp_session(
        conn,
        vp_id=vp_id,
        runtime_id="runtime.coder_vp.inprocess",
        status="active",
        session_id=f"{vp_id}.session",
        workspace_dir=str((tmp_path / vp_id.replace('.', '_')).resolve()),
        metadata={"lane": "coder_vp", "owner_user_id": "owner_primary"},
    )

    mission_a = f"{vp_id}.mission.a"
    mission_b = f"{vp_id}.mission.b"
    upsert_vp_mission(
        conn,
        mission_id=mission_a,
        vp_id=vp_id,
        status="completed",
        objective="Implement parser improvements",
        run_id="run-a",
        started_at=started_a,
        completed_at=completed_a,
    )
    upsert_vp_mission(
        conn,
        mission_id=mission_b,
        vp_id=vp_id,
        status="completed",
        objective="Refactor retry logic",
        run_id="run-b",
        started_at=started_b,
        completed_at=completed_b,
    )

    append_vp_event(
        conn,
        event_id=f"{mission_a}.dispatch",
        mission_id=mission_a,
        vp_id=vp_id,
        event_type="vp.mission.dispatched",
        payload={"run_id": "run-a"},
    )
    append_vp_event(
        conn,
        event_id=f"{mission_a}.completed",
        mission_id=mission_a,
        vp_id=vp_id,
        event_type="vp.mission.completed",
        payload={"trace_id": "trace-a"},
    )
    append_vp_event(
        conn,
        event_id=f"{mission_b}.dispatch",
        mission_id=mission_b,
        vp_id=vp_id,
        event_type="vp.mission.dispatched",
        payload={"run_id": "run-b"},
    )
    append_vp_event(
        conn,
        event_id=f"{mission_b}.fallback",
        mission_id=mission_b,
        vp_id=vp_id,
        event_type="vp.mission.fallback",
        payload={"reason": "vp_execution_error", "error": "simulated-vp-error"},
    )
    append_vp_event(
        conn,
        event_id=f"{mission_b}.completed",
        mission_id=mission_b,
        vp_id=vp_id,
        event_type="vp.mission.completed",
        payload={"trace_id": "trace-b"},
    )
    append_vp_session_event(
        conn,
        event_id=f"{vp_id}.session.created",
        vp_id=vp_id,
        event_type="vp.session.created",
        payload={"session_id": f"{vp_id}.session"},
    )
    append_vp_session_event(
        conn,
        event_id=f"{vp_id}.session.degraded",
        vp_id=vp_id,
        event_type="vp.session.degraded",
        payload={"reason": "lease_heartbeat_failed"},
    )
    append_vp_session_event(
        conn,
        event_id=f"{vp_id}.session.resumed",
        vp_id=vp_id,
        event_type="vp.session.resumed",
        payload={"reason": "lease_recovered"},
    )

    resp = client.get(f"/api/v1/ops/metrics/coder-vp?vp_id={vp_id}&mission_limit=20&event_limit=50")
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["vp_id"] == vp_id
    assert payload["session"]["vp_id"] == vp_id
    assert payload["mission_counts"]["completed"] == 2
    assert payload["event_counts"]["vp.mission.dispatched"] == 2
    assert payload["event_counts"]["vp.mission.completed"] == 2
    assert payload["event_counts"]["vp.mission.fallback"] == 1
    assert payload["session_event_counts"]["vp.session.degraded"] == 1
    assert payload["session_event_counts"]["vp.session.resumed"] == 1
    assert payload["fallback"]["missions_with_fallback"] == 1
    assert payload["fallback"]["missions_considered"] == 2
    assert payload["fallback"]["rate"] == 0.5
    assert payload["latency_seconds"]["count"] == 2
    assert payload["latency_seconds"]["p50_seconds"] is not None
    assert payload["recovery"]["attempts"] == 1
    assert payload["recovery"]["successes"] == 1
    assert payload["recovery"]["success_rate"] == 1.0
    assert payload["session_health"]["currently_orphaned"] is False
    assert payload["session_health"]["orphan_rate"] == 0.0
    assert any(item["event_type"] == "vp.session.degraded" for item in payload["recent_session_events"])
    assert any(item["fallback_seen"] for item in payload["recent_missions"])
    assert any(item["event_type"] == "vp.mission.fallback" for item in payload["recent_events"])


def test_ops_coder_vp_metrics_requires_runtime_db(client, monkeypatch):
    gateway = gateway_server.get_gateway()
    monkeypatch.setattr(gateway, "_runtime_db_conn", None)
    monkeypatch.setattr(gateway, "_coder_vp_db_conn", None)

    resp = client.get("/api/v1/ops/metrics/coder-vp")
    assert resp.status_code == 503
    assert "Runtime DB not initialized" in str(resp.json().get("detail"))


def test_ops_vp_dispatch_list_cancel_flow(client):
    dispatch_resp = client.post(
        "/api/v1/ops/vp/missions/dispatch",
        json={
            "vp_id": "vp.general.primary",
            "mission_type": "general_task",
            "objective": "Summarize current priorities and write next actions.",
            "constraints": {"target_path": "/tmp/vp_general_test_workspace"},
            "budget": {"max_minutes": 20},
            "idempotency_key": "vp-general-test-1",
        },
    )
    assert dispatch_resp.status_code == 200
    mission = dispatch_resp.json()["mission"]
    assert mission["vp_id"] == "vp.general.primary"
    assert mission["status"] == "queued"

    mission_id = mission["mission_id"]
    list_resp = client.get("/api/v1/ops/vp/missions?vp_id=vp.general.primary&status=queued&limit=20")
    assert list_resp.status_code == 200
    mission_ids = {item["mission_id"] for item in list_resp.json()["missions"]}
    assert mission_id in mission_ids

    sessions_resp = client.get("/api/v1/ops/vp/sessions?status=all&limit=20")
    assert sessions_resp.status_code == 200
    assert any(item["vp_id"] == "vp.general.primary" for item in sessions_resp.json()["sessions"])

    metrics_resp = client.get("/api/v1/ops/metrics/vp?vp_id=vp.general.primary&mission_limit=20&event_limit=20")
    assert metrics_resp.status_code == 200
    metrics_payload = metrics_resp.json()
    assert metrics_payload["vp_id"] == "vp.general.primary"
    assert metrics_payload["mission_counts"]["queued"] >= 1

    cancel_resp = client.post(
        f"/api/v1/ops/vp/missions/{mission_id}/cancel",
        json={"reason": "test_cancel"},
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancel_requested"


def test_ops_vp_sessions_reports_effective_stale_status(client):
    gateway = gateway_server.get_gateway()
    conn = gateway.get_vp_db_conn()
    assert conn is not None

    stale_vp_id = f"vp.general.stale.{time.time_ns()}"
    fresh_vp_id = f"vp.general.fresh.{time.time_ns()}"
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    now_ts = datetime.now(timezone.utc).isoformat()

    upsert_vp_session(
        conn=conn,
        vp_id=stale_vp_id,
        runtime_id="runtime.general.external",
        status="active",
        session_id=f"{stale_vp_id}.session",
        workspace_dir="/tmp/vp-stale-session",
    )
    conn.execute(
        "UPDATE vp_sessions SET last_heartbeat_at = ?, updated_at = ? WHERE vp_id = ?",
        (old_ts, old_ts, stale_vp_id),
    )
    conn.commit()

    upsert_vp_session(
        conn=conn,
        vp_id=fresh_vp_id,
        runtime_id="runtime.general.external",
        status="active",
        session_id=f"{fresh_vp_id}.session",
        workspace_dir="/tmp/vp-fresh-session",
        last_heartbeat_at=now_ts,
    )

    resp = client.get("/api/v1/ops/vp/sessions?status=all&limit=100")
    assert resp.status_code == 200
    rows = resp.json()["sessions"]
    stale_row = next(item for item in rows if item["vp_id"] == stale_vp_id)
    fresh_row = next(item for item in rows if item["vp_id"] == fresh_vp_id)

    assert stale_row["status"] == "active"
    assert stale_row["stale"] is True
    assert stale_row["effective_status"] == "stale"
    assert stale_row["stale_reason"] in {"heartbeat_timeout", "update_timeout"}

    assert fresh_row["status"] == "active"
    assert fresh_row["stale"] is False
    assert fresh_row["effective_status"] == "active"


def test_reconcile_stale_running_mission_marks_failed_and_emits_event(client):
    gateway = gateway_server.get_gateway()
    conn = gateway.get_vp_db_conn()
    assert conn is not None

    vp_id = f"vp.general.reconcile.failed.{time.time_ns()}"
    mission_id = f"{vp_id}.mission"
    source_session_id = f"session.{time.time_ns()}"
    source_turn_id = f"turn.{time.time_ns()}"
    now_utc = datetime.now(timezone.utc)
    started_at = (now_utc - timedelta(minutes=45)).isoformat()
    claim_expires_at = (now_utc - timedelta(minutes=30)).isoformat()

    upsert_vp_session(
        conn=conn,
        vp_id=vp_id,
        runtime_id="runtime.general.external",
        status="active",
        session_id=f"{vp_id}.session",
        workspace_dir="/tmp/vp-reconcile-failed",
    )
    upsert_vp_mission(
        conn=conn,
        mission_id=mission_id,
        vp_id=vp_id,
        status="running",
        objective="stale-running mission should be reconciled",
        payload={
            "source_session_id": source_session_id,
            "source_turn_id": source_turn_id,
            "reply_mode": "async",
        },
        started_at=started_at,
        claim_expires_at=claim_expires_at,
        worker_id="worker.reconcile.failed",
        result_ref=f"workspace:///tmp/vp/{mission_id}",
    )

    reconciled = gateway_server._reconcile_stale_vp_missions_once(
        conn,
        lane_label="external",
        stale_seconds=300,
    )
    assert reconciled == 1

    mission = get_vp_mission(conn, mission_id)
    assert mission is not None
    assert mission["status"] == "failed"
    assert mission["completed_at"]
    assert mission["claim_expires_at"] is None

    events = list_vp_events(conn, mission_id=mission_id, limit=10)
    reconcile_events = [row for row in events if row["event_type"] == "vp.mission.failed"]
    assert reconcile_events
    payload = json.loads(reconcile_events[-1]["payload_json"] or "{}")
    assert payload["reason"] == "stale_running_reconciled"
    assert payload["stale_reason"] == "claim_expired"
    assert payload["storage_lane"] == "external"
    assert payload["reconciled_by"] == "gateway.startup"
    assert payload["source_session_id"] == source_session_id
    assert payload["source_turn_id"] == source_turn_id
    assert payload["reply_mode"] == "async"


def test_reconcile_stale_running_cancel_requested_mission_marks_cancelled(client):
    gateway = gateway_server.get_gateway()
    conn = gateway.get_vp_db_conn()
    assert conn is not None

    vp_id = f"vp.general.reconcile.cancelled.{time.time_ns()}"
    mission_id = f"{vp_id}.mission"
    started_at = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat()

    upsert_vp_session(
        conn=conn,
        vp_id=vp_id,
        runtime_id="runtime.general.external",
        status="active",
        session_id=f"{vp_id}.session",
        workspace_dir="/tmp/vp-reconcile-cancelled",
    )
    upsert_vp_mission(
        conn=conn,
        mission_id=mission_id,
        vp_id=vp_id,
        status="running",
        objective="stale cancel requested mission should be reconciled",
        cancel_requested=True,
        started_at=started_at,
        worker_id="worker.reconcile.cancelled",
    )
    conn.execute(
        "UPDATE vp_missions SET updated_at = ? WHERE mission_id = ?",
        ((datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat(), mission_id),
    )
    conn.commit()

    reconciled = gateway_server._reconcile_stale_vp_missions_once(
        conn,
        lane_label="external",
        stale_seconds=120,
    )
    assert reconciled == 1

    mission = get_vp_mission(conn, mission_id)
    assert mission is not None
    assert mission["status"] == "cancelled"
    assert mission["completed_at"]
    assert mission["claim_expires_at"] is None

    events = list_vp_events(conn, mission_id=mission_id, limit=10)
    reconcile_events = [row for row in events if row["event_type"] == "vp.mission.cancelled"]
    assert reconcile_events
    payload = json.loads(reconcile_events[-1]["payload_json"] or "{}")
    assert payload["reason"] == "stale_running_reconciled"
    assert payload["storage_lane"] == "external"
    assert payload["reconciled_by"] == "gateway.startup"
    assert payload["previous_status"] == "running"
    assert payload["stale_reason"] in {"updated_at_timeout", "started_at_timeout"}


def test_ops_vp_dispatch_lock_returns_retryable_503(client, monkeypatch):
    def _raise_locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(gateway_server, "dispatch_mission_with_retry", _raise_locked)
    resp = client.post(
        "/api/v1/ops/vp/missions/dispatch",
        json={
            "vp_id": "vp.general.primary",
            "objective": "Trigger locked error",
        },
    )
    assert resp.status_code == 503
    payload = resp.json()
    detail = payload.get("detail", {})
    assert detail.get("code") == "vp_db_locked"
    assert detail.get("retryable") is True


def test_ops_vp_missions_duration_handles_mixed_timezone_values(client):
    gateway = gateway_server.get_gateway()
    conn = gateway.get_vp_db_conn()
    assert conn is not None

    vp_id = f"vp.general.tz.{time.time_ns()}"
    mission_id = f"{vp_id}.mission"
    upsert_vp_session(
        conn=conn,
        vp_id=vp_id,
        runtime_id="runtime.general.external",
        status="active",
        session_id=f"{vp_id}.session",
        workspace_dir="/tmp/vp-general-tz",
    )
    upsert_vp_mission(
        conn=conn,
        mission_id=mission_id,
        vp_id=vp_id,
        status="completed",
        objective="timezone normalization check",
        started_at="2026-02-21T10:00:00",
        completed_at="2026-02-21T10:00:05+00:00",
    )

    resp = client.get(f"/api/v1/ops/vp/missions?vp_id={vp_id}&status=completed&limit=20")
    assert resp.status_code == 200
    missions = resp.json()["missions"]
    mission = next(item for item in missions if item["mission_id"] == mission_id)
    assert mission["duration_seconds"] == 5.0


def test_ops_vp_general_worker_execution_and_workproduct_tracking(client, tmp_path):
    class _FakeGeneralistClient(VpClient):
        async def run_mission(self, *, mission, workspace_root):
            mission_id = str(mission.get("mission_id") or "mission")
            mission_dir = (workspace_root / mission_id).resolve()
            work_products_dir = mission_dir / "work_products"
            work_products_dir.mkdir(parents=True, exist_ok=True)
            artifact = work_products_dir / "summary.md"
            artifact.write_text(
                "# Mission Summary\n\nGenerated by test fake generalist worker.\n",
                encoding="utf-8",
            )
            return MissionOutcome(
                status="completed",
                result_ref=f"workspace://{mission_dir}",
                payload={"artifact_relpath": "work_products/summary.md"},
            )

    dispatch_resp = client.post(
        "/api/v1/ops/vp/missions/dispatch",
        json={
            "vp_id": "vp.general.primary",
            "mission_type": "general_task",
            "objective": "Create a small markdown summary work product.",
            "constraints": {},
            "budget": {"max_minutes": 5},
            "idempotency_key": "vp-general-worker-flow-1",
        },
    )
    assert dispatch_resp.status_code == 200
    mission_id = dispatch_resp.json()["mission"]["mission_id"]

    gateway = gateway_server.get_gateway()
    conn = getattr(gateway, "_vp_db_conn", None)
    assert conn is not None

    loop = VpWorkerLoop(
        conn=conn,
        vp_id="vp.general.primary",
        workspace_base=tmp_path,
        poll_interval_seconds=1,
        lease_ttl_seconds=60,
    )
    loop._client = _FakeGeneralistClient()  # type: ignore[assignment]

    asyncio.run(loop._tick())

    missions_resp = client.get("/api/v1/ops/vp/missions?vp_id=vp.general.primary&status=completed&limit=20")
    assert missions_resp.status_code == 200
    missions = missions_resp.json()["missions"]
    mission = next(item for item in missions if item["mission_id"] == mission_id)
    assert mission["status"] == "completed"
    result_ref = str(mission.get("result_ref") or "")
    assert result_ref.startswith("workspace://")

    workspace_path = Path(result_ref.replace("workspace://", "", 1))
    assert (workspace_path / "work_products" / "summary.md").exists()
    receipt_path = workspace_path / "mission_receipt.json"
    marker_path = workspace_path / "sync_ready.json"
    assert receipt_path.exists()
    assert marker_path.exists()

    receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt_payload["mission_id"] == mission_id
    assert receipt_payload["status"] == "completed"
    assert receipt_payload["outcome"]["payload"]["artifact_relpath"] == "work_products/summary.md"

    marker_payload = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker_payload["mission_id"] == mission_id
    assert marker_payload["state"] == "completed"
    assert marker_payload["ready"] is True

    metrics_resp = client.get("/api/v1/ops/metrics/vp?vp_id=vp.general.primary&mission_limit=20&event_limit=50")
    assert metrics_resp.status_code == 200
    metrics = metrics_resp.json()
    assert metrics["mission_counts"].get("completed", 0) >= 1
    assert metrics["event_counts"].get("vp.mission.started", 0) >= 1
    assert metrics["event_counts"].get("vp.mission.completed", 0) >= 1


def test_vp_event_bridge_injects_lifecycle_events_into_source_session_feed(client, tmp_path):
    session_resp = client.post("/api/v1/sessions", json={"user_id": "vp_bridge_user"})
    assert session_resp.status_code == 200
    source_session_id = session_resp.json()["session_id"]

    class _FakeGeneralistClient(VpClient):
        async def run_mission(self, *, mission, workspace_root):
            mission_id = str(mission.get("mission_id") or "mission")
            mission_dir = (workspace_root / mission_id).resolve()
            work_products_dir = mission_dir / "work_products"
            work_products_dir.mkdir(parents=True, exist_ok=True)
            (work_products_dir / "result.md").write_text("# Result\n\nok\n", encoding="utf-8")
            return MissionOutcome(
                status="completed",
                result_ref=f"workspace://{mission_dir}",
                payload={"artifact_relpath": "work_products/result.md"},
            )

    dispatch_resp = client.post(
        "/api/v1/ops/vp/missions/dispatch",
        json={
            "vp_id": "vp.general.primary",
            "mission_type": "general_task",
            "objective": "Bridge this mission back to session feed.",
            "constraints": {},
            "budget": {"max_minutes": 5},
            "idempotency_key": "vp-bridge-session-events-1",
            "source_session_id": source_session_id,
            "source_turn_id": "turn-bridge-1",
            "reply_mode": "async",
        },
    )
    assert dispatch_resp.status_code == 200
    mission_id = dispatch_resp.json()["mission"]["mission_id"]

    gateway = gateway_server.get_gateway()
    conn = gateway.get_vp_db_conn()
    assert conn is not None

    loop = VpWorkerLoop(
        conn=conn,
        vp_id="vp.general.primary",
        workspace_base=tmp_path,
        poll_interval_seconds=1,
        lease_ttl_seconds=60,
    )
    loop._client = _FakeGeneralistClient()  # type: ignore[assignment]
    asyncio.run(loop._tick())

    bridged_count = gateway_server._bridge_vp_events_once()
    assert bridged_count >= 1

    events_resp = client.get(f"/api/v1/system/events?session_id={source_session_id}")
    assert events_resp.status_code == 200
    events = events_resp.json()["events"]
    vp_events = [item for item in events if item.get("type") == "vp_mission_event"]
    assert vp_events
    assert any(item.get("payload", {}).get("mission_id") == mission_id for item in vp_events)
    completed_events = [
        item
        for item in vp_events
        if item.get("payload", {}).get("event_type") == "vp.mission.completed"
    ]
    assert completed_events
    assert any(
        str(item.get("payload", {}).get("result_ref") or "").startswith("workspace://")
        for item in vp_events
    )
    assert any(
        item.get("payload", {}).get("mission_status") == "completed"
        for item in completed_events
    )
    assert any(
        item.get("payload", {}).get("event_payload", {}).get("artifact_relpath")
        == "work_products/result.md"
        for item in completed_events
    )
    assert any(
        item.get("payload", {}).get("event_payload", {}).get("mission_receipt_relpath")
        == "mission_receipt.json"
        for item in completed_events
    )
    assert any(
        item.get("payload", {}).get("event_payload", {}).get("sync_ready_marker_relpath")
        == "sync_ready.json"
        for item in completed_events
    )


def test_vp_event_bridge_cursor_persists_across_restart_and_prevents_duplicates(client, tmp_path):
    session_resp = client.post("/api/v1/sessions", json={"user_id": "vp_bridge_cursor_user"})
    assert session_resp.status_code == 200
    source_session_id = session_resp.json()["session_id"]

    class _FakeGeneralistClient(VpClient):
        async def run_mission(self, *, mission, workspace_root):
            mission_id = str(mission.get("mission_id") or "mission")
            mission_dir = (workspace_root / mission_id).resolve()
            wp = mission_dir / "work_products"
            wp.mkdir(parents=True, exist_ok=True)
            (wp / "result.md").write_text("# Result\n\ncursor test\n", encoding="utf-8")
            return MissionOutcome(
                status="completed",
                result_ref=f"workspace://{mission_dir}",
                payload={"artifact_relpath": "work_products/result.md"},
            )

    dispatch_resp = client.post(
        "/api/v1/ops/vp/missions/dispatch",
        json={
            "vp_id": "vp.general.primary",
            "mission_type": "general_task",
            "objective": "Persist bridge cursor",
            "constraints": {},
            "budget": {"max_minutes": 5},
            "idempotency_key": "vp-bridge-cursor-1",
            "source_session_id": source_session_id,
            "source_turn_id": "turn-cursor-1",
            "reply_mode": "async",
        },
    )
    assert dispatch_resp.status_code == 200
    mission_id = dispatch_resp.json()["mission"]["mission_id"]

    gateway = gateway_server.get_gateway()
    conn = gateway.get_vp_db_conn()
    assert conn is not None

    loop = VpWorkerLoop(
        conn=conn,
        vp_id="vp.general.primary",
        workspace_base=tmp_path,
        poll_interval_seconds=1,
        lease_ttl_seconds=60,
    )
    loop._client = _FakeGeneralistClient()  # type: ignore[assignment]
    asyncio.run(loop._tick())

    first_count = gateway_server._bridge_vp_events_once()
    assert first_count >= 1

    cursor_before_restart = get_vp_bridge_cursor(conn, "gateway.session_feed")
    assert cursor_before_restart is not None and cursor_before_restart > 0

    # Simulate gateway restart: clear in-memory cursor + queue, then re-prime from DB.
    gateway_server._vp_event_bridge_last_rowid = 0
    gateway_server._system_events[source_session_id] = []
    gateway_server._vp_event_bridge_prime_cursor_to_latest()
    assert gateway_server._vp_event_bridge_last_rowid == cursor_before_restart

    duplicate_count = gateway_server._bridge_vp_events_once()
    assert duplicate_count == 0
    events_resp = client.get(f"/api/v1/system/events?session_id={source_session_id}")
    assert events_resp.status_code == 200
    assert events_resp.json()["events"] == []

    append_vp_event(
        conn=conn,
        event_id=f"vp-event-cursor-{time.time_ns()}",
        mission_id=mission_id,
        vp_id="vp.general.primary",
        event_type="vp.mission.progress",
        payload={"source_session_id": source_session_id, "note": "post-restart-progress"},
    )

    new_count = gateway_server._bridge_vp_events_once()
    assert new_count == 1
    cursor_after_new = get_vp_bridge_cursor(conn, "gateway.session_feed")
    assert cursor_after_new is not None and cursor_after_new > cursor_before_restart

    events_resp = client.get(f"/api/v1/system/events?session_id={source_session_id}")
    assert events_resp.status_code == 200
    events = events_resp.json()["events"]
    vp_events = [item for item in events if item.get("type") == "vp_mission_event"]
    assert any(item.get("payload", {}).get("event_type") == "vp.mission.progress" for item in vp_events)


def test_ops_vp_bridge_metrics_endpoint_reports_cursor_and_backlog(client):
    resp = client.get("/api/v1/ops/metrics/vp-bridge")
    assert resp.status_code == 200
    metrics = resp.json()["metrics"]
    assert metrics["cursor_key"] == "gateway.session_feed"
    assert metrics["db_ready"] is True
    assert isinstance(metrics["backlog_rows"], int)
    assert metrics["backlog_rows"] >= 0
    assert "events_bridged_total" in metrics
    assert "cycles" in metrics


def test_ops_vp_bridge_metrics_endpoint_handles_missing_vp_db(client, monkeypatch):
    gateway = gateway_server.get_gateway()
    monkeypatch.setattr(gateway, "_vp_db_conn", None)

    resp = client.get("/api/v1/ops/metrics/vp-bridge")
    assert resp.status_code == 200
    metrics = resp.json()["metrics"]
    assert metrics["db_ready"] is False
    assert metrics["persisted_cursor"] is None


def test_ops_vp_bridge_cursor_update_controls_persisted_cursor(client):
    gateway = gateway_server.get_gateway()
    conn = gateway.get_vp_db_conn()
    assert conn is not None

    vp_id = f"vp.general.bridge.control.{time.time_ns()}"
    mission_id = f"{vp_id}.mission"
    upsert_vp_session(
        conn=conn,
        vp_id=vp_id,
        runtime_id="runtime.general.external",
        status="active",
        session_id=f"{vp_id}.session",
        workspace_dir="/tmp/vp-bridge-control",
    )
    upsert_vp_mission(
        conn=conn,
        mission_id=mission_id,
        vp_id=vp_id,
        status="queued",
        objective="bridge cursor control seed mission",
    )
    append_vp_event(
        conn=conn,
        event_id=f"{mission_id}.evt.1",
        mission_id=mission_id,
        vp_id=vp_id,
        event_type="vp.mission.dispatched",
        payload={"source_session_id": "seed-session"},
    )
    append_vp_event(
        conn=conn,
        event_id=f"{mission_id}.evt.2",
        mission_id=mission_id,
        vp_id=vp_id,
        event_type="vp.mission.started",
        payload={"source_session_id": "seed-session"},
    )

    reset_zero = client.post("/api/v1/ops/vp/bridge/cursor", json={"action": "reset_to_zero"})
    assert reset_zero.status_code == 200
    payload_zero = reset_zero.json()
    assert payload_zero["update"]["target_rowid"] == 0
    assert payload_zero["metrics"]["persisted_cursor"] == 0
    assert payload_zero["metrics"]["manual_updates"] >= 1

    set_resp = client.post("/api/v1/ops/vp/bridge/cursor", json={"action": "set", "rowid": 1})
    assert set_resp.status_code == 200
    payload_set = set_resp.json()
    assert payload_set["update"]["target_rowid"] == 1
    assert payload_set["metrics"]["persisted_cursor"] == 1

    latest_resp = client.post("/api/v1/ops/vp/bridge/cursor", json={"action": "reset_to_latest"})
    assert latest_resp.status_code == 200
    payload_latest = latest_resp.json()
    assert payload_latest["update"]["target_rowid"] == payload_latest["update"]["max_rowid"]
    assert payload_latest["metrics"]["persisted_cursor"] == payload_latest["update"]["max_rowid"]

    clamp_resp = client.post("/api/v1/ops/vp/bridge/cursor", json={"action": "set", "rowid": 999999})
    assert clamp_resp.status_code == 200
    payload_clamp = clamp_resp.json()
    assert payload_clamp["update"]["clamped"] is True
    assert payload_clamp["update"]["target_rowid"] == payload_clamp["update"]["max_rowid"]


def test_ops_vp_bridge_cursor_update_validation_errors(client):
    missing_rowid = client.post("/api/v1/ops/vp/bridge/cursor", json={"action": "set"})
    assert missing_rowid.status_code == 400
    assert "rowid is required" in str(missing_rowid.json().get("detail", ""))

    negative_rowid = client.post("/api/v1/ops/vp/bridge/cursor", json={"action": "set", "rowid": -1})
    assert negative_rowid.status_code == 400
    assert "rowid must be >= 0" in str(negative_rowid.json().get("detail", ""))

    invalid_action = client.post("/api/v1/ops/vp/bridge/cursor", json={"action": "rewind"})
    assert invalid_action.status_code == 400
    assert "Unsupported action" in str(invalid_action.json().get("detail", ""))


def test_ops_vp_bridge_cursor_update_requires_vp_db(client, monkeypatch):
    gateway = gateway_server.get_gateway()
    monkeypatch.setattr(gateway, "_vp_db_conn", None)

    resp = client.post("/api/v1/ops/vp/bridge/cursor", json={"action": "reset_to_zero"})
    assert resp.status_code == 503
    assert "VP DB not initialized" in str(resp.json().get("detail", ""))


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
    assert policy["memory"]["scope"] == "direct_only"
    assert policy["memory"]["sessionMemory"] is True

    patch_resp = client.patch(
        "/api/v1/sessions/session_policy/policy",
        json={
            "patch": {
                "identity_mode": "operator_proxy",
                "autonomy_mode": "guarded",
                "memory": {
                    "enabled": True,
                    "sessionMemory": False,
                    "sources": ["memory"],
                    "scope": "all",
                },
            }
        },
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["policy"]["identity_mode"] == "operator_proxy"
    assert patch_resp.json()["policy"]["memory"]["scope"] == "all"
    assert patch_resp.json()["policy"]["memory"]["sessionMemory"] is False
    assert patch_resp.json()["policy"]["memory"]["sources"] == ["memory"]

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
        '{"memory":{"enabled":true,"sessionMemory":false,"sources":["memory"],"scope":"all"},"user_id":"tester_full"}',
        encoding="utf-8",
    )
    (default_dir / "session_policy.json").write_text(
        '{"memory":{"enabled":true,"sessionMemory":true,"sources":["memory","sessions"],"scope":"direct_only"},"user_id":"tester_default"}',
        encoding="utf-8",
    )

    full_resp = client.get("/api/v1/ops/sessions?memory_mode=memory_only")
    assert full_resp.status_code == 200
    full_ids = {item["session_id"] for item in full_resp.json()["sessions"]}
    assert "session_full_mem" in full_ids
    assert "session_default_mem" not in full_ids

    for item in full_resp.json()["sessions"]:
        if item["session_id"] == "session_full_mem":
            assert item["memory_mode"] == "memory_only"
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


def test_ops_calendar_events_with_running_cron_not_marked_missed(client, tmp_path):
    gateway_server._cron_service = CronService(
        gateway_server.get_gateway(),
        tmp_path,
    )
    cron_ws = tmp_path / "cron_running_workspace"
    cron_ws.mkdir(parents=True, exist_ok=True)
    job = gateway_server._cron_service.add_job(
        user_id="calendar_owner",
        workspace_dir=str(cron_ws),
        command="long running task",
        run_at=time.time() - 300,
        enabled=True,
    )

    # Simulate in-flight execution for this scheduled occurrence.
    scheduled_at = float(job.run_at or time.time())
    gateway_server._cron_service.running_jobs.add(job.job_id)
    gateway_server._cron_service.running_job_scheduled_at[job.job_id] = scheduled_at

    resp = client.get("/api/v1/ops/calendar/events?source=cron&view=week")
    assert resp.status_code == 200
    payload = resp.json()
    matching = [
        item for item in payload.get("events", [])
        if item.get("source") == "cron" and item.get("source_ref") == job.job_id
    ]
    assert matching, "Expected cron event for running job"
    assert matching[0]["status"] == "running"
    assert not any(
        item.get("source") == "cron" and item.get("source_ref") == job.job_id and item.get("status") == "missed"
        for item in payload.get("events", [])
    )


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


def test_calendar_heartbeat_summary_filters_stale_active_connections(monkeypatch):
    monkeypatch.setattr(gateway_server, "_heartbeat_service", None)
    now_ts = time.time()
    stale_activity = datetime.fromtimestamp(now_ts - 7200, timezone.utc).isoformat()
    summary = {
        "session_id": "session_20260220_082954_0e5713db",
        "source": "chat",
        "active_connections": 1,
        "active_runs": 0,
        "last_activity": stale_activity,
    }
    assert gateway_server._calendar_should_include_heartbeat_summary(summary, now_ts) is False


def test_calendar_heartbeat_projection_uses_distinct_titles_and_skips_stale(monkeypatch, tmp_path):
    now_ts = time.time()
    fresh_session = "session_20260220_082954_0e5713db"
    stale_session = "session_20260220_081952_38d5bac6"
    fresh_workspace = tmp_path / fresh_session
    stale_workspace = tmp_path / stale_session
    fresh_workspace.mkdir(parents=True, exist_ok=True)
    stale_workspace.mkdir(parents=True, exist_ok=True)

    class _StubOps:
        def list_sessions(self, status_filter: str = "all"):
            return [
                {
                    "session_id": fresh_session,
                    "owner": "owner_primary",
                    "source": "chat",
                    "channel": "chat",
                    "workspace_dir": str(fresh_workspace),
                    "active_connections": 1,
                    "active_runs": 0,
                    "last_activity": datetime.fromtimestamp(now_ts - 60, timezone.utc).isoformat(),
                },
                {
                    "session_id": stale_session,
                    "owner": "owner_primary",
                    "source": "chat",
                    "channel": "chat",
                    "workspace_dir": str(stale_workspace),
                    "active_connections": 1,
                    "active_runs": 0,
                    "last_activity": datetime.fromtimestamp(now_ts - 7200, timezone.utc).isoformat(),
                },
            ]

    monkeypatch.setattr(gateway_server, "_ops_service", _StubOps())
    monkeypatch.setattr(gateway_server, "_heartbeat_service", None)

    events, always_running = gateway_server._calendar_project_heartbeat_events(
        start_ts=now_ts - 60,
        end_ts=now_ts + (4 * 3600),
        timezone_name="America/Chicago",
        owner=None,
    )

    # Fresh session is included in both timeline/always-running; stale session is excluded.
    refs = {str(item.get("source_ref")) for item in always_running}
    assert fresh_session in refs
    assert stale_session not in refs

    titles = [str(item.get("title") or "") for item in events]
    assert any("0e5713db" in title for title in titles)


def test_ops_work_thread_decision_roundtrip(client):
    session_id = "session_delivery_workflow"

    iterate_resp = client.post(
        "/api/v1/ops/work-threads/decide",
        json={
            "session_id": session_id,
            "decision": "iterate",
            "note": "Continue with tighter acceptance criteria.",
            "metadata": {"source": "test"},
        },
    )
    assert iterate_resp.status_code == 200
    iterate_thread = iterate_resp.json()["thread"]
    assert iterate_thread["session_id"] == session_id
    assert iterate_thread["decision"] == "iterate"
    assert iterate_thread["status"] == "active"
    assert iterate_thread["patch_version"] == 2
    assert len(iterate_thread.get("history", [])) == 1

    list_resp = client.get(f"/api/v1/ops/work-threads?session_id={session_id}")
    assert list_resp.status_code == 200
    listed = list_resp.json().get("threads", [])
    assert len(listed) == 1
    assert listed[0]["thread_id"] == iterate_thread["thread_id"]

    promote_resp = client.post(
        "/api/v1/ops/work-threads/decide",
        json={
            "session_id": session_id,
            "decision": "promote",
            "note": "Promotion approved after checks.",
        },
    )
    assert promote_resp.status_code == 200
    promote_thread = promote_resp.json()["thread"]
    assert promote_thread["thread_id"] == iterate_thread["thread_id"]
    assert promote_thread["decision"] == "promote"
    assert promote_thread["status"] == "promoted"
    assert promote_thread["patch_version"] == 2
    assert len(promote_thread.get("history", [])) == 2


def test_ops_work_thread_rejects_invalid_decision(client):
    resp = client.post(
        "/api/v1/ops/work-threads/decide",
        json={
            "session_id": "session_delivery_invalid",
            "decision": "ship_it",
        },
    )
    assert resp.status_code == 400
    assert "Unsupported decision" in resp.text
