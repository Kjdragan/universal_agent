import json
import sqlite3
from datetime import datetime, timezone

import pytest

from universal_agent import main as agent_main
from universal_agent.durable.ledger import ToolCallLedger
from universal_agent.durable.migrations import ensure_schema


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

    old_runtime = agent_main.runtime_db_conn
    old_ledger = agent_main.tool_ledger
    old_run_id = agent_main.run_id
    old_step_id = agent_main.current_step_id
    try:
        agent_main.runtime_db_conn = conn
        agent_main.tool_ledger = ledger
        agent_main.run_id = run_id
        agent_main.current_step_id = step_id
        agent_main._invalid_side_effect_warnings.clear()

        result = await agent_main.on_pre_tool_use_ledger(
            {"tool_name": "GMAIL_SEND_EMAIL", "tool_input": tool_input},
            "tool-guardrail-2",
            {},
        )
    finally:
        agent_main.runtime_db_conn = old_runtime
        agent_main.tool_ledger = old_ledger
        agent_main.run_id = old_run_id
        agent_main.current_step_id = old_step_id
        conn.close()

    assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
