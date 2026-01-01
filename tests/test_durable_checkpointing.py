import sqlite3

from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.checkpointing import save_checkpoint, load_last_checkpoint


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    return conn


def _seed_run(conn: sqlite3.Connection, run_id: str, step_id: str) -> None:
    conn.execute(
        """
        INSERT INTO runs (
            run_id, created_at, updated_at, status, entrypoint, run_spec_json
        ) VALUES (?, datetime('now'), datetime('now'), 'running', 'cli', '{}')
        """,
        (run_id,),
    )
    conn.execute(
        """
        INSERT INTO run_steps (
            step_id, run_id, step_index, created_at, updated_at, status, phase
        ) VALUES (?, ?, 1, datetime('now'), datetime('now'), 'running', 'test')
        """,
        (step_id, run_id),
    )
    conn.commit()


def test_checkpoint_round_trip():
    conn = _conn()
    run_id = "run-abc"
    step_id = "step-abc"
    _seed_run(conn, run_id, step_id)

    checkpoint_id = save_checkpoint(
        conn,
        run_id=run_id,
        step_id=step_id,
        checkpoint_type="step_boundary",
        state_snapshot={"phase": "test"},
        cursor={"last_tool_call_id": "tool-1"},
    )
    row = load_last_checkpoint(conn, run_id)
    assert row is not None
    assert row["checkpoint_id"] == checkpoint_id
    assert row["checkpoint_type"] == "step_boundary"
