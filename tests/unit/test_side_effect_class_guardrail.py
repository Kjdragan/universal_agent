from datetime import datetime, timezone
import json
import sqlite3

import pytest

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


@pytest.mark.anyio
async def test_invalid_side_effect_class_defaults_to_external(monkeypatch):
    conn = _setup_conn()
    run_id = "run-guardrail-1"
    step_id = "step-guardrail-1"
    _insert_run_and_step(conn, run_id, step_id)

    ledger = ToolCallLedger(conn)
    tool_input = {"to": "person@example.com", "subject": "Hello"}

    receipt, _ = ledger.prepare_tool_call(
        tool_call_id="tool-guardrail-1",
        run_id=run_id,
        step_id=step_id,
        tool_name="GMAIL_SEND_EMAIL",
        tool_namespace="composio",
        tool_input=tool_input,
    )
    assert receipt is None
    ledger.mark_succeeded("tool-guardrail-1", {"ok": True})

    conn.execute(
        "UPDATE tool_calls SET side_effect_class = ? WHERE tool_call_id = ?",
        ("bogus", "tool-guardrail-1"),
    )
    conn.commit()

    ctx = SessionContext(
        run_id=run_id,
        current_step_id=step_id,
        runtime_db_conn=conn,
        tool_ledger=ledger,
    )
    token = set_ctx(ctx)
    try:
        agent_main._invalid_side_effect_warnings.clear()

        result = await agent_main.on_pre_tool_use_ledger(
            {"tool_name": "GMAIL_SEND_EMAIL", "tool_input": tool_input},
            "tool-guardrail-2",
            {},
        )
    finally:
        reset_ctx(token)
        conn.close()

    assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
