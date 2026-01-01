import json
import sqlite3
from datetime import datetime, timezone

from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.ledger import ToolCallLedger
from universal_agent.durable.normalize import hash_normalized_json


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


def test_idempotency_key_stable():
    args_a = {"b": 2, "a": 1, "nested": {"y": "z", "x": "w"}}
    args_b = {"nested": {"x": "w", "y": "z"}, "a": 1, "b": 2}
    assert hash_normalized_json(args_a) == hash_normalized_json(args_b)


def test_ledger_dedupe_returns_receipt():
    conn = _setup_conn()
    run_id = "run-1"
    step_id = "step-1"
    _insert_run_and_step(conn, run_id, step_id)

    ledger = ToolCallLedger(conn)
    tool_input = {"to": "person@example.com", "subject": "Hello"}

    receipt, idem_key = ledger.prepare_tool_call(
        tool_call_id="tool-1",
        run_id=run_id,
        step_id=step_id,
        tool_name="GMAIL_SEND_EMAIL",
        tool_namespace="composio",
        tool_input=tool_input,
    )
    assert receipt is None

    ledger.mark_succeeded("tool-1", {"ok": True, "message_id": "msg-1"})

    receipt2, idem_key2 = ledger.prepare_tool_call(
        tool_call_id="tool-2",
        run_id=run_id,
        step_id=step_id,
        tool_name="GMAIL_SEND_EMAIL",
        tool_namespace="composio",
        tool_input=tool_input,
    )
    assert idem_key == idem_key2
    assert receipt2 is not None
    assert receipt2.status == "succeeded"
