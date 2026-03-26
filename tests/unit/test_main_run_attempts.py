import sqlite3
from pathlib import Path

from universal_agent import main as agent_main
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import (
    create_run_attempt,
    get_run,
    get_run_attempt,
    upsert_run,
)
from universal_agent.session_ctx import SessionContext, reset_ctx, set_ctx


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    return conn


def test_ensure_current_run_attempt_reuses_latest_attempt_for_resume():
    conn = _conn()
    upsert_run(conn, "run-resume", "cli", {"workspace_dir": "/tmp/run-resume"})
    original_attempt = create_run_attempt(conn, "run-resume", status="running")
    run_row = get_run(conn, "run-resume")

    token = set_ctx(
        SessionContext(
            run_id="run-resume",
            runtime_db_conn=conn,
            trace={},
        )
    )
    try:
        attempt_id = agent_main._ensure_current_run_attempt(
            status="running",
            existing_run_row=run_row,
            resume_requested=True,
        )
    finally:
        reset_ctx(token)

    assert attempt_id == original_attempt
    refreshed_run = get_run(conn, "run-resume")
    assert refreshed_run["attempt_count"] == 1


def test_provider_session_updates_current_attempt_and_run():
    conn = _conn()
    upsert_run(conn, "run-provider-main", "cli", {"workspace_dir": "/tmp/provider"})
    attempt_id = create_run_attempt(conn, "run-provider-main", status="running")

    token = set_ctx(
        SessionContext(
            run_id="run-provider-main",
            runtime_db_conn=conn,
            current_run_attempt_id=attempt_id,
            trace={},
        )
    )
    try:
        agent_main._maybe_update_provider_session("provider-session-123")
        run_row = get_run(conn, "run-provider-main")
        attempt_row = get_run_attempt(conn, attempt_id)
        assert run_row["provider_session_id"] == "provider-session-123"
        assert attempt_row["provider_session_id"] == "provider-session-123"

        agent_main._invalidate_provider_session("resume session invalid")
        run_row = get_run(conn, "run-provider-main")
        attempt_row = get_run_attempt(conn, attempt_id)
        assert run_row["provider_session_id"] is None
        assert attempt_row["provider_session_id"] is None
    finally:
        reset_ctx(token)


def test_run_status_helpers_update_current_attempt_status():
    conn = _conn()
    upsert_run(conn, "run-status-main", "cli", {"workspace_dir": "/tmp/status"})
    attempt_id = create_run_attempt(conn, "run-status-main", status="running")

    token = set_ctx(
        SessionContext(
            run_id="run-status-main",
            runtime_db_conn=conn,
            current_run_attempt_id=attempt_id,
            trace={},
        )
    )
    try:
        agent_main._mark_run_waiting_for_human("need operator review")
        run_row = get_run(conn, "run-status-main")
        attempt_row = get_run_attempt(conn, attempt_id)
        assert run_row["status"] == "waiting_for_human"
        assert attempt_row["status"] == "waiting_for_human"
        assert attempt_row["failure_reason"] == "need operator review"
        assert Path("/tmp/status/attempts/001/attempt_meta.json").exists()

        upsert_run(
            conn,
            "run-success-main",
            "cli",
            {"workspace_dir": "/tmp/success"},
            status="running",
        )
        success_attempt_id = create_run_attempt(
            conn,
            "run-success-main",
            status="running",
        )
        reset_ctx(token)
        token = set_ctx(
            SessionContext(
                run_id="run-success-main",
                runtime_db_conn=conn,
                current_run_attempt_id=success_attempt_id,
                trace={},
            )
        )
        agent_main._maybe_mark_run_succeeded()
        run_row = get_run(conn, "run-success-main")
        attempt_row = get_run_attempt(conn, success_attempt_id)
        assert run_row["status"] == "succeeded"
        assert attempt_row["status"] == "succeeded"
    finally:
        reset_ctx(token)


def test_mark_run_paused_updates_current_attempt_status():
    conn = _conn()
    upsert_run(conn, "run-paused-main", "cli", {"workspace_dir": "/tmp/paused"})
    attempt_id = create_run_attempt(conn, "run-paused-main", status="running")

    token = set_ctx(
        SessionContext(
            run_id="run-paused-main",
            runtime_db_conn=conn,
            current_run_attempt_id=attempt_id,
            trace={},
        )
    )
    try:
        agent_main._mark_run_paused("interrupt_checkpoint")
        run_row = get_run(conn, "run-paused-main")
        attempt_row = get_run_attempt(conn, attempt_id)
        assert run_row["status"] == "paused"
        assert attempt_row["status"] == "paused"
        assert attempt_row["failure_reason"] == "interrupt_checkpoint"
        assert Path("/tmp/paused/attempts/001/attempt_meta.json").exists()
    finally:
        reset_ctx(token)


def test_workspace_timestamp_fragment_supports_run_session_and_harness_prefixes():
    assert agent_main._workspace_timestamp_fragment("/tmp/run_20260324_010203_abcd1234") == "20260324_010203_abcd1234"
    assert agent_main._workspace_timestamp_fragment("/tmp/session_20260324_010203_abcd1234") == "20260324_010203_abcd1234"
    assert agent_main._workspace_timestamp_fragment("/tmp/harness_20260324_010203_abcd1234") == "20260324_010203_abcd1234"
    assert agent_main._workspace_timestamp_fragment("/tmp/custom_workspace") == "custom_workspace"


def test_default_urw_workspace_path_uses_run_prefix():
    path = agent_main._default_urw_workspace_path()
    assert path.name.startswith("run_")
    assert path.parent.name == "urw_sessions"


def test_ensure_current_run_attempt_bootstraps_missing_parent_run():
    conn = _conn()

    token = set_ctx(
        SessionContext(
            run_id="run-missing-main",
            runtime_db_conn=conn,
            observer_workspace_dir="/tmp/missing-parent-run",
            trace={},
        )
    )
    try:
        attempt_id = agent_main._ensure_current_run_attempt(status="running")
    finally:
        reset_ctx(token)

    assert attempt_id is not None
    run_row = get_run(conn, "run-missing-main")
    assert run_row is not None
    assert run_row["status"] == "running"
    assert run_row["workspace_dir"] == "/tmp/missing-parent-run"
    attempt_row = get_run_attempt(conn, attempt_id)
    assert attempt_row is not None
    assert attempt_row["run_id"] == "run-missing-main"
