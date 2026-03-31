import asyncio
import sqlite3

from universal_agent.durable.ledger import ToolCallLedger
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import start_step, upsert_run
from universal_agent.hooks import AgentHookSet


def _run(coro):
    return asyncio.run(coro)


def _runtime_conn(*, run_id: str, run_kind: str = "", with_task_evidence: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    upsert_run(
        conn,
        run_id=run_id,
        entrypoint="test",
        run_spec={},
        run_kind=run_kind or None,
    )
    if with_task_evidence:
        step_id = f"step:{run_id}"
        start_step(conn, run_id, step_id, 1)
        ledger = ToolCallLedger(conn)
        ledger.prepare_tool_call(
            tool_call_id=f"tool:{run_id}:task",
            run_id=run_id,
            step_id=step_id,
            tool_name="task",
            tool_namespace="claude_code",
            raw_tool_name="Task",
            tool_input={"subagent_type": "research-specialist", "prompt": "collect data"},
        )
    return conn


def _pre_task_stop(hooks: AgentHookSet, task_id: str | None):
    tool_input = {}
    if task_id is not None:
        tool_input["task_id"] = task_id
    return _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "TaskStop",
                "tool_input": tool_input,
            },
            "tool-taskstop",
            {},
        )
    )


# ── Existing Tests ─────────────────────────────────────────────────


def test_blocks_task_stop_with_missing_task_id():
    hooks = AgentHookSet(run_id="unit-taskstop-missing")
    result = _pre_task_stop(hooks, None)
    assert result.get("decision") == "block"
    assert "Missing `task_id`" in str(result.get("systemMessage", ""))


def test_blocks_task_stop_with_placeholder_id():
    hooks = AgentHookSet(run_id="unit-taskstop-placeholder")
    result = _pre_task_stop(hooks, "all")
    assert result.get("decision") == "block"
    assert "placeholder" in str(result.get("systemMessage", "")).lower()


def test_blocks_task_stop_with_session_id():
    hooks = AgentHookSet(run_id="unit-taskstop-session")
    result = _pre_task_stop(hooks, "session_20260309_073910_8099458a")
    assert result.get("decision") == "block"
    assert "session/run identifier" in str(result.get("systemMessage", "")).lower()


def test_blocks_task_stop_with_fabricated_id_prefix():
    hooks = AgentHookSet(run_id="unit-taskstop-fabricated")
    result = _pre_task_stop(hooks, "dummy-stop")
    assert result.get("decision") == "block"
    assert "fabricated" in str(result.get("systemMessage", "")).lower() or "placeholder" in str(
        result.get("systemMessage", "")
    ).lower()


def test_blocks_task_stop_with_weak_synthetic_task_id():
    hooks = AgentHookSet(run_id="unit-taskstop-weak-id")
    result = _pre_task_stop(hooks, "task_1")
    assert result.get("decision") == "block"
    assert "untrusted" in str(result.get("systemMessage", "")).lower()


def test_blocks_task_stop_with_natural_language_task_id():
    hooks = AgentHookSet(run_id="unit-taskstop-natural-language")
    result = _pre_task_stop(hooks, "research-specialist")
    assert result.get("decision") == "block"
    assert "untrusted" in str(result.get("systemMessage", "")).lower()


def test_allows_task_stop_with_concrete_task_id():
    conn = _runtime_conn(run_id="unit-taskstop-allow", with_task_evidence=True)
    hooks = AgentHookSet(run_id="unit-taskstop-allow", runtime_db_conn=conn)
    result = _pre_task_stop(hooks, "task_01HZYQ7QF1")
    assert result == {}


def test_blocks_duplicate_task_stop_after_successful_stop():
    conn = _runtime_conn(run_id="unit-taskstop-duplicate", with_task_evidence=True)
    hooks = AgentHookSet(run_id="unit-taskstop-duplicate", runtime_db_conn=conn)
    first = _pre_task_stop(hooks, "task_01HZYQ7QF1")
    assert first == {}

    _run(
        hooks.on_post_tool_use_ledger(
            {
                "tool_name": "TaskStop",
                "tool_input": {"task_id": "task_01HZYQ7QF1"},
                "tool_result": {"is_error": False, "content": [{"type": "text", "text": "ok"}]},
                "is_error": False,
            },
            "tool-taskstop-post",
            {},
        )
    )

    second = _pre_task_stop(hooks, "task_01HZYQ7QF1")
    assert second.get("decision") == "block"
    assert "duplicate" in str(second.get("systemMessage", "")).lower()


# ── New Tests: Fabricated IDs from real incident ──────────────────


def test_blocks_task_research_001():
    """IDs like 'task_research_001' are human-readable composites, not SDK IDs."""
    hooks = AgentHookSet(run_id="unit-taskstop-research-001")
    result = _pre_task_stop(hooks, "task_research_001")
    assert result.get("decision") == "block"
    assert "human-readable" in str(result.get("systemMessage", "")).lower()


def test_blocks_task_report_001():
    """IDs like 'task_report_001' are human-readable composites, not SDK IDs."""
    hooks = AgentHookSet(run_id="unit-taskstop-report-001")
    result = _pre_task_stop(hooks, "task_report_001")
    assert result.get("decision") == "block"
    assert "human-readable" in str(result.get("systemMessage", "")).lower()


def test_blocks_task_russia_ukraine_research_001():
    """Long descriptive IDs like 'task_russia_ukraine_research_001' must be blocked."""
    hooks = AgentHookSet(run_id="unit-taskstop-ru-research")
    result = _pre_task_stop(hooks, "task_russia_ukraine_research_001")
    assert result.get("decision") == "block"
    assert "human-readable" in str(result.get("systemMessage", "")).lower()


def test_blocks_task_russia_ukraine_research_no_digits():
    """IDs like 'task_russia_ukraine_research' have no digits — should be blocked."""
    hooks = AgentHookSet(run_id="unit-taskstop-ru-no-digits")
    result = _pre_task_stop(hooks, "task_russia_ukraine_research")
    assert result.get("decision") == "block"
    assert "human-readable" in str(result.get("systemMessage", "")).lower()


def test_blocks_ru_news_with_date():
    """IDs like 'ru_news_20260317' are human-composed word+date tokens."""
    hooks = AgentHookSet(run_id="unit-taskstop-ru-news-date")
    result = _pre_task_stop(hooks, "ru_news_20260317")
    assert result.get("decision") == "block"
    assert "human-composed" in str(result.get("systemMessage", "")).lower()


def test_blocks_short_id_with_digits():
    """Short IDs like 'task_01' or 'stop_42' are too short to be real SDK IDs."""
    hooks = AgentHookSet(run_id="unit-taskstop-short")
    result = _pre_task_stop(hooks, "task_01")
    assert result.get("decision") == "block"


def test_allows_real_sdk_ulid():
    """Real SDK ULIDs like 'task_01HZYQ7QF1' should pass through."""
    conn = _runtime_conn(run_id="unit-taskstop-ulid", with_task_evidence=True)
    hooks = AgentHookSet(run_id="unit-taskstop-ulid", runtime_db_conn=conn)
    result = _pre_task_stop(hooks, "task_01HZYQ7QF1")
    assert result == {}


def test_allows_real_toolu_style_id():
    """Real tool-use IDs like 'toolu_vrtx_01234ABCdef56789' should pass through."""
    conn = _runtime_conn(run_id="unit-taskstop-toolu", with_task_evidence=True)
    hooks = AgentHookSet(run_id="unit-taskstop-toolu", runtime_db_conn=conn)
    result = _pre_task_stop(hooks, "toolu_vrtx_01234ABCdef56789")
    assert result == {}


def test_allows_real_uuid_format():
    """Real UUID-format task IDs should pass through."""
    conn = _runtime_conn(run_id="unit-taskstop-uuid", with_task_evidence=True)
    hooks = AgentHookSet(run_id="unit-taskstop-uuid", runtime_db_conn=conn)
    result = _pre_task_stop(hooks, "550e8400-e29b-41d4-a716-446655440000")
    assert result == {}


# ── Circuit-breaker Tests ────────────────────────────────────────


def test_circuit_breaker_after_consecutive_failures():
    """After 2 consecutive failures, the circuit-breaker should trip."""
    hooks = AgentHookSet(run_id="unit-taskstop-circuit-breaker")
    # First failure
    r1 = _pre_task_stop(hooks, "task_fake_001")
    assert r1.get("decision") == "block"
    # Second failure
    r2 = _pre_task_stop(hooks, "task_report_002")
    assert r2.get("decision") == "block"
    # Third attempt: circuit-breaker should trip with different message
    r3 = _pre_task_stop(hooks, "task_another_003")
    assert r3.get("decision") == "block"
    assert "circuit-breaker" in str(r3.get("systemMessage", "")).lower()


def test_circuit_breaker_resets_on_valid_stop():
    """A valid TaskStop resets the circuit-breaker counter."""
    conn = _runtime_conn(run_id="unit-taskstop-cb-reset", with_task_evidence=True)
    hooks = AgentHookSet(run_id="unit-taskstop-cb-reset", runtime_db_conn=conn)
    # One failure
    r1 = _pre_task_stop(hooks, "task_fake_001")
    assert r1.get("decision") == "block"
    # Valid stop — should pass and reset counter
    r2 = _pre_task_stop(hooks, "task_01HZYQ7QF1")
    assert r2 == {}
    # Another failure — counter starts from 0, so should NOT trip circuit-breaker
    r3 = _pre_task_stop(hooks, "task_bad_001")
    assert r3.get("decision") == "block"
    assert "circuit-breaker" not in str(r3.get("systemMessage", "")).lower()


def test_blocks_taskstop_in_todo_execution_even_with_valid_sdk_id():
    conn = _runtime_conn(
        run_id="unit-taskstop-todo",
        run_kind="todo_execution",
        with_task_evidence=True,
    )
    hooks = AgentHookSet(run_id="unit-taskstop-todo", runtime_db_conn=conn)
    result = _pre_task_stop(hooks, "task_01HZYQ7QF1")
    assert result.get("decision") == "block"
    text = str(result.get("systemMessage", "")).lower()
    assert "task hub" in text
    assert "task_hub_task_action" in text
    assert "complete" in text
    assert "review" in text


def test_blocks_taskstop_in_email_triage_even_with_valid_sdk_id():
    conn = _runtime_conn(
        run_id="unit-taskstop-email-triage",
        run_kind="email_triage",
        with_task_evidence=True,
    )
    hooks = AgentHookSet(run_id="unit-taskstop-email-triage", runtime_db_conn=conn)
    result = _pre_task_stop(hooks, "task_01HZYQ7QF1")
    assert result.get("decision") == "block"
    text = str(result.get("systemMessage", "")).lower()
    assert "triage-only" in text
    assert "dedicated todo executor" in text
    assert "final delivery" in text


def test_blocks_taskstop_in_heartbeat_lane_even_with_valid_sdk_id():
    conn = _runtime_conn(
        run_id="unit-taskstop-heartbeat",
        run_kind="heartbeat_health_check",
        with_task_evidence=True,
    )
    hooks = AgentHookSet(run_id="unit-taskstop-heartbeat", runtime_db_conn=conn)
    result = _pre_task_stop(hooks, "task_01HZYQ7QF1")
    assert result.get("decision") == "block"
    assert "heartbeat/proactive workflow" in str(result.get("systemMessage", "")).lower()


def test_blocks_valid_sdk_id_without_prior_task_evidence():
    conn = _runtime_conn(run_id="unit-taskstop-no-sdk", run_kind="general")
    hooks = AgentHookSet(run_id="unit-taskstop-no-sdk", runtime_db_conn=conn)
    result = _pre_task_stop(hooks, "task_01HZYQ7QF1")
    assert result.get("decision") == "block"
    assert "no active sdk-managed task is known in this run" in str(result.get("systemMessage", "")).lower()
