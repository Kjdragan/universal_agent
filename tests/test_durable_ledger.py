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
    entry = ledger.get_tool_call("tool-1")
    assert entry is not None
    assert entry["replay_policy"] == "REPLAY_EXACT"

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


def test_multi_execute_idempotency_ignores_session_metadata():
    conn = _setup_conn()
    run_id = "run-multi"
    step_id = "step-multi"
    _insert_run_and_step(conn, run_id, step_id)

    ledger = ToolCallLedger(conn)
    tool_call = {
        "tool_slug": "GMAIL_SEND_EMAIL",
        "arguments": {
            "recipient_email": "person@example.com",
            "subject": "Durability Relaunch Test Report",
            "body": "Test email",
            "attachment": {"name": "report.pdf", "mimetype": "application/pdf", "s3key": "s3://key"},
        },
    }
    tool_input_a = {
        "session_id": "session-a",
        "current_step": "SENDING_EMAIL",
        "current_step_metric": "1/1 emails",
        "sync_response_to_workbench": False,
        "thought": "first attempt",
        "tools": [tool_call],
    }
    receipt, key_a = ledger.prepare_tool_call(
        tool_call_id="tool-multi-a",
        run_id=run_id,
        step_id=step_id,
        tool_name="COMPOSIO_MULTI_EXECUTE_TOOL",
        tool_namespace="composio",
        tool_input=tool_input_a,
    )
    assert receipt is None
    ledger.mark_succeeded("tool-multi-a", {"ok": True, "message_id": "msg-1"})

    tool_input_b = {
        "session_id": "session-b",
        "current_step": "SENDING_EMAIL",
        "current_step_metric": "1/1 emails",
        "sync_response_to_workbench": False,
        "thought": "retry attempt",
        "tools": [tool_call],
    }
    receipt_b, key_b = ledger.prepare_tool_call(
        tool_call_id="tool-multi-b",
        run_id=run_id,
        step_id=step_id,
        tool_name="COMPOSIO_MULTI_EXECUTE_TOOL",
        tool_namespace="composio",
        tool_input=tool_input_b,
    )
    assert key_a == key_b
    assert receipt_b is not None
    assert receipt_b.status == "succeeded"


def test_prepare_allow_duplicate_creates_new_row():
    conn = _setup_conn()
    run_id = "run-1b"
    step_id = "step-1b"
    _insert_run_and_step(conn, run_id, step_id)

    ledger = ToolCallLedger(conn)
    tool_input = {"query": "alpha"}

    receipt, key1 = ledger.prepare_tool_call(
        tool_call_id="tool-1b",
        run_id=run_id,
        step_id=step_id,
        tool_name="COMPOSIO_GET_ITEM",
        tool_namespace="composio",
        tool_input=tool_input,
    )
    assert receipt is None
    ledger.mark_succeeded("tool-1b", {"ok": True})

    receipt2, key2 = ledger.prepare_tool_call(
        tool_call_id="tool-2b",
        run_id=run_id,
        step_id=step_id,
        tool_name="COMPOSIO_GET_ITEM",
        tool_namespace="composio",
        tool_input=tool_input,
        allow_duplicate=True,
        idempotency_nonce="tool-2b",
    )
    assert receipt2 is None
    assert key1 != key2
    row = ledger.get_tool_call("tool-2b")
    assert row is not None
    assert row["status"] == "prepared"


def test_mark_abandoned_on_resume_updates_status():
    conn = _setup_conn()
    run_id = "run-2"
    step_id = "step-2"
    _insert_run_and_step(conn, run_id, step_id)

    ledger = ToolCallLedger(conn)
    receipt, _ = ledger.prepare_tool_call(
        tool_call_id="tool-3",
        run_id=run_id,
        step_id=step_id,
        tool_name="Task",
        tool_namespace="claude_code",
        tool_input={"subagent_type": "report-creation-expert", "prompt": "Hello"},
    )
    assert receipt is None

    ledger.mark_abandoned_on_resume("tool-3", "relaunch_on_resume")
    row = ledger.get_tool_call("tool-3")
    assert row is not None
    assert row["status"] == "abandoned_on_resume"


def test_relaunch_idempotency_key_unique_per_call():
    conn = _setup_conn()
    run_id = "run-3"
    step_id = "step-3"
    _insert_run_and_step(conn, run_id, step_id)

    ledger = ToolCallLedger(conn)
    tool_input = {"subagent_type": "report-creation-expert", "prompt": "Hello"}

    _, key1 = ledger.prepare_tool_call(
        tool_call_id="tool-4",
        run_id=run_id,
        step_id=step_id,
        tool_name="task",
        tool_namespace="claude_code",
        tool_input=tool_input,
    )
    _, key2 = ledger.prepare_tool_call(
        tool_call_id="tool-5",
        run_id=run_id,
        step_id=step_id,
        tool_name="task",
        tool_namespace="claude_code",
        tool_input=tool_input,
    )
    assert key1 != key2


def test_mark_replay_status_updates_column():
    conn = _setup_conn()
    run_id = "run-4"
    step_id = "step-4"
    _insert_run_and_step(conn, run_id, step_id)

    ledger = ToolCallLedger(conn)
    receipt, _ = ledger.prepare_tool_call(
        tool_call_id="tool-6",
        run_id=run_id,
        step_id=step_id,
        tool_name="bash",
        tool_namespace="claude_code",
        tool_input={"command": "echo hi"},
    )
    assert receipt is None

    ledger.mark_replay_status("tool-6", "succeeded")
    row = ledger.get_tool_call("tool-6")
    assert row is not None
    assert row["replay_status"] == "succeeded"


def test_pending_receipt_promote_marks_succeeded():
    conn = _setup_conn()
    run_id = "run-5"
    step_id = "step-5"
    _insert_run_and_step(conn, run_id, step_id)

    ledger = ToolCallLedger(conn)
    receipt, _ = ledger.prepare_tool_call(
        tool_call_id="tool-7",
        run_id=run_id,
        step_id=step_id,
        tool_name="GMAIL_SEND_EMAIL",
        tool_namespace="composio",
        tool_input={"to": "a@example.com", "subject": "Hello"},
    )
    assert receipt is None

    recorded = ledger.record_receipt_pending(
        "tool-7", {"ok": True, "message_id": "msg-123"}, "msg-123"
    )
    assert recorded is True

    promoted = ledger.promote_pending_receipt("tool-7")
    assert promoted is True
    row = ledger.get_tool_call("tool-7")
    assert row is not None
    assert row["status"] == "succeeded"
