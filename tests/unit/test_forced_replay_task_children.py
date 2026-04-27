import asyncio
from datetime import datetime, timezone
import json
import sqlite3

from universal_agent import main as agent_main
from universal_agent.durable.ledger import ToolCallLedger
from universal_agent.durable.migrations import ensure_schema
from universal_agent.session_ctx import SessionContext, reset_ctx, set_ctx


def _setup_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    return conn


def _insert_run_and_step(conn: sqlite3.Connection, run_id: str, step_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO runs (
            run_id, created_at, updated_at, status, entrypoint, run_spec_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, now, now, "running", "cli", json.dumps({"objective": "test"})),
    )
    conn.execute(
        """
        INSERT INTO run_steps (
            step_id, run_id, step_index, created_at, updated_at, status, phase
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (step_id, run_id, 1, now, now, "running", "test"),
    )
    conn.commit()


def test_forced_replay_allows_task_children():
    conn = _setup_conn()
    run_id = "run-1"
    step_id = "step-1"
    _insert_run_and_step(conn, run_id, step_id)
    ledger = ToolCallLedger(conn)

    expected = {
        "tool_call_id": "forced-task",
        "tool_name": "task",
        "tool_namespace": "claude_code",
        "raw_tool_name": "Task",
        "step_id": step_id,
        "tool_input": {"subagent_type": "report", "prompt": "do work"},
    }

    ctx = SessionContext(
        run_id=run_id,
        current_step_id=step_id,
        runtime_db_conn=conn,
        tool_ledger=ledger,
        forced_tool_queue=[expected],
        forced_tool_active_ids={"tool-use-task": expected},
    )
    token = set_ctx(ctx)
    try:
        result = asyncio.run(
            agent_main.on_pre_tool_use_ledger(
                {
                    "tool_name": "mcp__local_toolkit__write_local_file",
                    "tool_input": {
                        "path": "work_products/out.txt",
                        "content": "Test content here",
                    },
                },
                "tool-use-write",
                {},
            )
        )
        assert result == {}

        result = asyncio.run(
            agent_main.on_pre_tool_use_ledger(
                {
                    "tool_name": "Write",
                    "tool_input": {
                        "file_path": "work_products/out.txt",
                        "content": "Test content here for validation",
                    },
                },
                "tool-use-write-claude",
                {},
            )
        )
        assert result == {}
    finally:
        reset_ctx(token)
