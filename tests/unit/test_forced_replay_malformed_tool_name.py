import asyncio
import json
import sqlite3
from datetime import datetime, timezone

from universal_agent import main as agent_main
from universal_agent.durable.ledger import ToolCallLedger
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import get_run_status
from universal_agent.session_ctx import SessionContext, set_ctx, reset_ctx


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


def test_forced_replay_malformed_tool_name_does_not_mark_waiting():
    conn = _setup_conn()
    run_id = "run-malformed-1"
    step_id = "step-malformed-1"
    _insert_run_and_step(conn, run_id, step_id)
    ledger = ToolCallLedger(conn)

    expected = {
        "tool_call_id": "forced-composio",
        "tool_name": "COMPOSIO_MULTI_EXECUTE_TOOL",
        "tool_namespace": "composio",
        "raw_tool_name": "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
        "step_id": step_id,
        "tool_input": {
            "tools": [
                {
                    "tool_slug": "GMAIL_SEND_EMAIL",
                    "arguments": {"recipient_email": "kevin.dragan@outlook.com"},
                }
            ]
        },
    }

    ctx = SessionContext(
        run_id=run_id,
        current_step_id=step_id,
        runtime_db_conn=conn,
        tool_ledger=ledger,
        forced_tool_queue=[expected],
        forced_tool_active_ids={},
        forced_tool_mode_active=True,
    )
    token = set_ctx(ctx)
    try:
        malformed_name = (
            "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL-<arg_key>tools</arg_key>"
            '<arg_value>[{"tool_slug":"GMAIL_SEND_EMAIL","arguments":{}}]</arg_value>'
        )
        result = asyncio.run(
            agent_main.on_pre_tool_use_ledger(
                {"tool_name": malformed_name, "tool_input": {}},
                "tool-use-malformed",
                {},
            )
        )
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
        assert get_run_status(conn, run_id) == "running"
        assert len(ctx.forced_tool_queue) == 1
    finally:
        reset_ctx(token)
        conn.close()
