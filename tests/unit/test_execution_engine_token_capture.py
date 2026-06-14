"""Integration test for ProcessTurnAdapter._record_turn_tokens.

Proves the adapter-level in-process token capture end-to-end — the exact path
daemon Simone / in-process VP turns take — against a real temp activity_state.db.
This is the verification that waiting on the live daemon could NOT provide:
daemon turns in the observation window were cancelled before completing (no SDK
ResultMessage to capture), so "no row" and "silently broken" looked identical.
This test removes that ambiguity by driving the capture method directly with a
realistic populated trace and asserting a row lands.
"""

from __future__ import annotations

import pytest


def _adapter(tmp_path, run_source: str = "heartbeat"):
    from universal_agent.execution_engine import EngineConfig, ProcessTurnAdapter

    cfg = EngineConfig(workspace_dir=str(tmp_path), user_id="test")
    cfg.__dict__["_run_source"] = run_source  # how the gateway sets it
    return ProcessTurnAdapter(cfg)


@pytest.fixture
def activity_conn(tmp_path, monkeypatch):
    """Point the activity DB at a temp file and return a connection with schema."""
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "activity.db"))
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.task_hub import ensure_schema

    conn = connect_runtime_db(get_activity_db_path())
    ensure_schema(conn)
    return conn


def test_adapter_records_turn_tokens(tmp_path, activity_conn):
    adapter = _adapter(tmp_path, "heartbeat")
    # Two per-iteration ResultMessages (usage is per-iteration, cost is cumulative).
    adapter._trace = {
        "sdk_result_messages": [
            {"usage": {"input_tokens": 100, "output_tokens": 10,
                       "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 0},
             "total_cost_usd": 0.5, "num_turns": 2, "model_usage": {"glm-5.1": {}}},
            {"usage": {"input_tokens": 50, "output_tokens": 5,
                       "cache_read_input_tokens": 2000},
             "total_cost_usd": 0.8, "num_turns": 2},
        ],
        "session_id": "sess-x",
    }

    adapter._record_turn_tokens(0)

    row = activity_conn.execute(
        "SELECT source, principal, input_tokens, output_tokens, "
        "cache_read_input_tokens, total_cost_usd, session_id, model "
        "FROM token_usage_events"
    ).fetchone()
    assert row is not None, "adapter must write a token_usage_events row"
    assert row[0] == "cli-in-process"
    assert row[1] == "simone"
    assert row[2] == 150          # 100 + 50, SUMMED per-iteration (not tail)
    assert row[3] == 15
    assert row[4] == 7000         # cache_read summed (the dominant term)
    assert row[5] == pytest.approx(0.8)   # last cumulative cost, baseline 0
    assert row[6] == "sess-x"
    assert row[7] == "glm-5.1"


def test_adapter_records_only_this_turns_delta(tmp_path, activity_conn):
    """Reused trace across turns: start_idx makes adjacent turns disjoint."""
    adapter = _adapter(tmp_path, "todo_dispatcher")  # observed-live run_source
    adapter._trace = {
        "sdk_result_messages": [
            {"usage": {"input_tokens": 999}, "total_cost_usd": 0.5},  # prior turn
        ],
    }
    start_idx = len(adapter._trace["sdk_result_messages"])  # snapshot at turn entry
    adapter._trace["sdk_result_messages"].append(
        {"usage": {"input_tokens": 30}, "total_cost_usd": 0.9}      # this turn
    )

    adapter._record_turn_tokens(start_idx)

    rows = activity_conn.execute(
        "SELECT input_tokens, principal, total_cost_usd FROM token_usage_events"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 30                          # only this turn (not 999)
    assert rows[0][1] == "simone"                    # todo_dispatcher -> simone
    assert rows[0][2] == pytest.approx(0.9 - 0.5)    # cost delta vs prior baseline


def test_adapter_empty_delta_writes_no_row(tmp_path, activity_conn):
    adapter = _adapter(tmp_path)
    adapter._trace = {"sdk_result_messages": []}
    adapter._record_turn_tokens(0)
    assert activity_conn.execute(
        "SELECT COUNT(*) FROM token_usage_events"
    ).fetchone()[0] == 0


def test_adapter_record_never_raises_on_bad_trace(tmp_path, activity_conn):
    # A telemetry failure must NEVER propagate into the turn/teardown.
    adapter = _adapter(tmp_path)
    adapter._trace = None  # type: ignore[assignment]
    adapter._record_turn_tokens(0)  # must not raise
    assert activity_conn.execute(
        "SELECT COUNT(*) FROM token_usage_events"
    ).fetchone()[0] == 0
