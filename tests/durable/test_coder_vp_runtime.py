import sqlite3
import json

from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import (
    get_vp_mission,
    get_vp_session,
    list_vp_events,
    list_vp_session_events,
)
from universal_agent.vp import CoderVPRuntime


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    return conn


def test_coder_vp_route_decision_respects_flags(monkeypatch, tmp_path):
    conn = _conn()
    runtime = CoderVPRuntime(conn, workspace_base=tmp_path)
    explicit_codie_request = "Use CODIE to fix this Python bug in the parser"

    monkeypatch.delenv("UA_ENABLE_CODER_VP", raising=False)
    monkeypatch.delenv("UA_DISABLE_CODER_VP", raising=False)
    monkeypatch.delenv("UA_CODER_VP_SHADOW_MODE", raising=False)
    monkeypatch.delenv("UA_CODER_VP_FORCE_FALLBACK", raising=False)

    default_on = runtime.route_decision(explicit_codie_request)
    assert default_on.use_coder_vp is True
    assert default_on.reason == "eligible"

    no_explicit_intent = runtime.route_decision("Please fix this Python bug in the parser")
    assert no_explicit_intent.use_coder_vp is False
    assert no_explicit_intent.reason == "intent_not_coding"

    utility_bash = runtime.route_decision(
        "Write a bash command to recursively find .py files and print their line counts"
    )
    assert utility_bash.use_coder_vp is False
    assert utility_bash.reason == "intent_not_coding"

    utility_python = runtime.route_decision(
        "Give me a small helper function in Python to slugify a string"
    )
    assert utility_python.use_coder_vp is False
    assert utility_python.reason == "intent_not_coding"

    scoped_work = runtime.route_decision(
        "Build an end-to-end Python script project with integration test coverage"
    )
    assert scoped_work.use_coder_vp is False
    assert scoped_work.reason == "intent_not_coding"

    latest_regression = runtime.route_decision(
        "Search for the latest information on the Russia-Ukraine war over the past five days"
    )
    assert latest_regression.use_coder_vp is False
    assert latest_regression.reason == "intent_not_coding"

    internal_system_task = runtime.route_decision(
        "CODIE implement a Python refactor in src/universal_agent/gateway_server.py and adjust Simone heartbeat calendar behavior"
    )
    assert internal_system_task.use_coder_vp is False
    assert internal_system_task.reason == "internal_system_request"

    monkeypatch.setenv("UA_DISABLE_CODER_VP", "1")
    disabled = runtime.route_decision(explicit_codie_request)
    assert disabled.use_coder_vp is False
    assert disabled.reason == "feature_disabled"

    monkeypatch.delenv("UA_DISABLE_CODER_VP", raising=False)

    monkeypatch.setenv("UA_ENABLE_CODER_VP", "1")
    enabled = runtime.route_decision(explicit_codie_request)
    assert enabled.use_coder_vp is True
    assert enabled.intent_matched is True

    monkeypatch.setenv("UA_CODER_VP_SHADOW_MODE", "1")
    shadow = runtime.route_decision(explicit_codie_request)
    assert shadow.use_coder_vp is False
    assert shadow.shadow_mode is True
    assert shadow.reason == "shadow_mode"

    monkeypatch.setenv("UA_CODER_VP_FORCE_FALLBACK", "1")
    forced = runtime.route_decision(explicit_codie_request)
    assert forced.use_coder_vp is False
    assert forced.force_fallback is True
    assert forced.reason == "forced_fallback"


def test_coder_vp_session_and_mission_lifecycle(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ENABLE_CODER_VP", "1")

    conn = _conn()
    runtime = CoderVPRuntime(conn, workspace_base=tmp_path)

    session = runtime.ensure_session(lease_owner="simone-control", owner_user_id="owner_primary")
    assert session is not None
    assert session["vp_id"] == "vp.coder.primary"
    session_events = list_vp_session_events(conn, vp_id="vp.coder.primary")
    assert any(row["event_type"] == "vp.session.created" for row in session_events)

    runtime.bind_session_identity(session_id="vp_lane_session_1")
    session = get_vp_session(conn, "vp.coder.primary")
    assert session is not None
    assert session["session_id"] == "vp_lane_session_1"
    session_events = list_vp_session_events(conn, vp_id="vp.coder.primary")
    assert any(row["event_type"] == "vp.session.resumed" for row in session_events)

    mission_id = runtime.start_mission(
        objective="Implement a robust parser",
        run_id="run-123",
        trace_id="trace-abc",
    )
    mission = get_vp_mission(conn, mission_id)
    assert mission is not None
    assert mission["status"] == "running"

    runtime.append_progress(mission_id, summary="Started coding")
    mission_workspace = (tmp_path / "vp_coder_primary" / mission_id).resolve()
    runtime.mark_mission_completed(
        mission_id,
        result_ref=f"workspace://{mission_workspace}",
        trace_id="trace-final",
    )

    mission = get_vp_mission(conn, mission_id)
    assert mission is not None
    assert mission["status"] == "completed"
    assert str(mission["result_ref"] or "") == f"workspace://{mission_workspace}"

    events = list_vp_events(conn, mission_id=mission_id)
    event_types = [row["event_type"] for row in events]
    assert "vp.mission.dispatched" in event_types
    assert "vp.mission.progress" in event_types
    assert "vp.mission.completed" in event_types
    completed_event = next(row for row in events if row["event_type"] == "vp.mission.completed")
    completed_payload = json.loads(str(completed_event["payload_json"] or "{}"))
    assert completed_payload.get("mission_receipt_relpath") == "mission_receipt.json"
    assert completed_payload.get("sync_ready_marker_relpath") == "sync_ready.json"
    assert (mission_workspace / "mission_receipt.json").exists()
    assert (mission_workspace / "sync_ready.json").exists()


def test_coder_vp_failed_mission_writes_finalize_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ENABLE_CODER_VP", "1")

    conn = _conn()
    runtime = CoderVPRuntime(conn, workspace_base=tmp_path)
    runtime.ensure_session(lease_owner="simone-control", owner_user_id="owner_primary")
    mission_id = runtime.start_mission(
        objective="Implement parser and handle failure path",
        run_id="run-failed-1",
        trace_id="trace-start",
    )
    runtime.mark_mission_failed(
        mission_id,
        error_message="forced failure",
        trace_id="trace-failed",
    )

    mission = get_vp_mission(conn, mission_id)
    assert mission is not None
    assert mission["status"] == "failed"
    mission_workspace = (tmp_path / "vp_coder_primary" / mission_id).resolve()
    assert str(mission["result_ref"] or "") == f"workspace://{mission_workspace}"

    events = list_vp_events(conn, mission_id=mission_id)
    failed_event = next(row for row in events if row["event_type"] == "vp.mission.failed")
    failed_payload = json.loads(str(failed_event["payload_json"] or "{}"))
    assert failed_payload.get("mission_receipt_relpath") == "mission_receipt.json"
    assert failed_payload.get("sync_ready_marker_relpath") == "sync_ready.json"

    assert (mission_workspace / "mission_receipt.json").exists()
    assert (mission_workspace / "sync_ready.json").exists()


def test_coder_vp_session_seeds_codie_soul(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ENABLE_CODER_VP", "1")

    conn = _conn()
    runtime = CoderVPRuntime(conn, workspace_base=tmp_path)
    session = runtime.ensure_session(lease_owner="simone-control", owner_user_id="owner_primary")

    assert session is not None
    soul_path = tmp_path / "vp_coder_primary" / "SOUL.md"
    assert soul_path.exists()
    content = soul_path.read_text(encoding="utf-8")
    assert "# CODIE" in content


def test_coder_vp_lease_heartbeat_failure_marks_degraded(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ENABLE_CODER_VP", "1")

    conn = _conn()
    runtime = CoderVPRuntime(conn, workspace_base=tmp_path)
    session = runtime.ensure_session(lease_owner="simone-control", owner_user_id="owner_primary")
    assert session is not None

    heartbeat_ok = runtime.heartbeat_session_lease(lease_owner="different-owner")
    assert heartbeat_ok is False

    session = get_vp_session(conn, "vp.coder.primary")
    assert session is not None
    assert session["status"] == "degraded"

    session_events = list_vp_session_events(conn, vp_id="vp.coder.primary")
    assert any(row["event_type"] == "vp.session.degraded" for row in session_events)


def test_coder_vp_release_lease_sets_idle_and_session_event(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ENABLE_CODER_VP", "1")

    conn = _conn()
    runtime = CoderVPRuntime(conn, workspace_base=tmp_path)
    session = runtime.ensure_session(lease_owner="simone-control", owner_user_id="owner_primary")
    assert session is not None

    runtime.release_session_lease(lease_owner="simone-control")
    session = get_vp_session(conn, "vp.coder.primary")
    assert session is not None
    assert session["status"] == "idle"
    assert session["lease_owner"] is None

    session_events = list_vp_session_events(conn, vp_id="vp.coder.primary")
    assert any(
        row["event_type"] == "vp.session.resumed"
        and "lease_released" in str(row["payload_json"] or "")
        for row in session_events
    )
