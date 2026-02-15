import json
import sqlite3

from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import (
    get_run,
    get_step_count,
    get_vp_mission,
    get_vp_session,
    list_vp_events,
    list_vp_missions,
    list_vp_sessions,
    release_vp_session_lease,
    start_step,
    complete_step,
    upsert_run,
    upsert_vp_mission,
    upsert_vp_session,
    update_run_status,
    update_run_provider_session,
    acquire_vp_session_lease,
    heartbeat_vp_session_lease,
    append_vp_event,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    return conn


def test_run_lifecycle_and_steps():
    conn = _conn()
    run_id = "run-123"
    spec = {"objective": "demo"}

    upsert_run(conn, run_id, "cli", spec, status="running")
    row = get_run(conn, run_id)
    assert row is not None
    assert json.loads(row["run_spec_json"]) == spec

    update_run_status(conn, run_id, "succeeded")
    row = get_run(conn, run_id)
    assert row["status"] == "succeeded"

    start_step(conn, run_id, "step-1", 1, phase="plan")
    assert get_step_count(conn, run_id) == 1

    complete_step(conn, "step-1", "succeeded")
    step = conn.execute(
        "SELECT status FROM run_steps WHERE step_id = ?",
        ("step-1",),
    ).fetchone()
    assert step["status"] == "succeeded"


def test_update_run_provider_session_fields():
    conn = _conn()
    run_id = "run-provider"
    spec = {"objective": "demo"}

    upsert_run(conn, run_id, "cli", spec, status="running")
    update_run_provider_session(conn, run_id, "session-1", forked_from="base-1")

    row = get_run(conn, run_id)
    assert row["provider_session_id"] == "session-1"
    assert row["provider_session_forked_from"] == "base-1"
    assert row["provider_session_last_seen_at"] is not None


def test_vp_session_registry_lifecycle_and_leases():
    conn = _conn()

    upsert_vp_session(
        conn,
        vp_id="vp.coder.primary",
        runtime_id="runtime-main",
        status="idle",
        session_id="session-abc",
        workspace_dir="/tmp/vp-coder",
        metadata={"lane": "vp"},
    )

    row = get_vp_session(conn, "vp.coder.primary")
    assert row is not None
    assert row["runtime_id"] == "runtime-main"
    assert row["status"] == "idle"
    assert row["session_id"] == "session-abc"
    assert json.loads(row["metadata_json"]) == {"lane": "vp"}

    active_rows = list_vp_sessions(conn, statuses=["idle"])
    assert len(active_rows) == 1
    assert active_rows[0]["vp_id"] == "vp.coder.primary"

    acquired = acquire_vp_session_lease(
        conn,
        vp_id="vp.coder.primary",
        lease_owner="simone-control",
        lease_ttl_seconds=60,
    )
    assert acquired is True

    beat = heartbeat_vp_session_lease(
        conn,
        vp_id="vp.coder.primary",
        lease_owner="simone-control",
        lease_ttl_seconds=60,
    )
    assert beat is True

    release_vp_session_lease(conn, "vp.coder.primary", "simone-control")
    row = get_vp_session(conn, "vp.coder.primary")
    assert row["lease_owner"] is None
    assert row["lease_expires_at"] is None


def test_vp_mission_and_event_tracking():
    conn = _conn()

    upsert_vp_session(
        conn,
        vp_id="vp.coder.primary",
        runtime_id="runtime-main",
        status="active",
        session_id="session-abc",
    )

    upsert_vp_mission(
        conn,
        mission_id="mission-1",
        vp_id="vp.coder.primary",
        status="queued",
        objective="Implement parser",
        budget={"max_runtime_minutes": 30},
    )
    upsert_vp_mission(
        conn,
        mission_id="mission-1",
        vp_id="vp.coder.primary",
        status="running",
        objective="Implement parser",
        run_id="run-123",
    )

    mission = get_vp_mission(conn, "mission-1")
    assert mission is not None
    assert mission["status"] == "running"
    assert mission["run_id"] == "run-123"
    assert json.loads(mission["budget_json"]) == {"max_runtime_minutes": 30}

    missions = list_vp_missions(conn, "vp.coder.primary", statuses=["running"])
    assert len(missions) == 1
    assert missions[0]["mission_id"] == "mission-1"

    append_vp_event(
        conn,
        event_id="event-1",
        mission_id="mission-1",
        vp_id="vp.coder.primary",
        event_type="mission.progress",
        payload={"summary": "Started coding"},
    )
    append_vp_event(
        conn,
        event_id="event-2",
        mission_id="mission-1",
        vp_id="vp.coder.primary",
        event_type="mission.completed",
        payload={"summary": "Done"},
    )

    events = list_vp_events(conn, mission_id="mission-1")
    assert len(events) == 2
    assert events[0]["event_type"] == "mission.progress"
    assert events[1]["event_type"] == "mission.completed"
    assert json.loads(events[0]["payload_json"]) == {"summary": "Started coding"}
