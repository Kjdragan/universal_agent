import sqlite3

from universal_agent import main as agent_main
from universal_agent.session_ctx import SessionContext, reset_ctx, set_ctx


def _set_runtime_step(phase: str = "replay") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE run_steps (
            step_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO run_steps (step_id, phase) VALUES (?, ?)",
        ("step-1", phase),
    )
    return conn


def test_should_not_trigger_without_env(monkeypatch):
    monkeypatch.delenv("UA_TEST_CRASH_AFTER_TOOL", raising=False)
    monkeypatch.delenv("UA_TEST_CRASH_AFTER_TOOL_CALL_ID", raising=False)
    monkeypatch.delenv("UA_TEST_CRASH_STAGE", raising=False)
    monkeypatch.delenv("UA_TEST_CRASH_AFTER_PHASE", raising=False)
    monkeypatch.delenv("UA_TEST_CRASH_AFTER_STEP", raising=False)

    should_crash, context = agent_main._should_trigger_test_crash(
        raw_tool_name="GMAIL_SEND_EMAIL",
        tool_call_id="call-1",
        stage="after_tool_success_before_ledger_commit",
    )

    assert should_crash is False
    assert context == {}


def test_crash_matches_normalized_tool_name(monkeypatch):
    monkeypatch.setenv("UA_TEST_CRASH_AFTER_TOOL", "gmail_send_email")

    should_crash, context = agent_main._should_trigger_test_crash(
        raw_tool_name="GMAIL_SEND_EMAIL",
        tool_call_id="call-1",
        stage="after_tool_success_before_ledger_commit",
    )

    assert should_crash is True
    assert context["normalized_tool_name"] == "gmail_send_email"


def test_crash_rejects_stage_mismatch(monkeypatch):
    monkeypatch.setenv("UA_TEST_CRASH_AFTER_TOOL", "gmail_send_email")
    monkeypatch.setenv(
        "UA_TEST_CRASH_AFTER_STAGE", "after_ledger_mark_succeeded"
    )  # Different stage

    should_crash, _ = agent_main._should_trigger_test_crash(
        raw_tool_name="GMAIL_SEND_EMAIL",
        tool_call_id="call-1",
        stage="after_tool_success_before_ledger_commit",
    )

    assert should_crash is False


def test_crash_matches_phase_and_step(monkeypatch):
    conn = _set_runtime_step(phase="replay")
    ctx = SessionContext(
        run_id="test-run",
        current_step_id="step-1",
        runtime_db_conn=conn,
    )
    token = set_ctx(ctx)
    try:
        monkeypatch.setenv("UA_TEST_CRASH_AFTER_PHASE", "replay")
        monkeypatch.setenv("UA_TEST_CRASH_AFTER_STEP", "step-1")

        should_crash, context = agent_main._should_trigger_test_crash(
            raw_tool_name="TASK",
            tool_call_id="call-1",
            stage="after_tool_success_before_ledger_commit",
        )

        assert should_crash is True
        assert context["current_phase"] == "replay"
    finally:
        reset_ctx(token)
        conn.close()
