import json
import sqlite3

from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import (
    upsert_run,
    start_step,
    complete_step,
    get_run,
    get_step_count,
    update_run_status,
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
