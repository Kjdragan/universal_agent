"""Unit tests for services/principal_token_tracking (lane B in-process capture).

Encodes the empirically-verified ResultMessage semantics (2026-06-14, real
Simone trace): `usage` is PER-ITERATION (sum the slice, never take the tail),
`total_cost_usd` is cumulative (delta vs baseline), and cache tokens are summed.
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent.services import principal_token_tracking as ptt

# Real-shape delta slice modelled on the VPS trace: per-iteration usage that does
# NOT grow monotonically, plus trailing no-op messages, plus cumulative cost.
_DELTA = [
    {"usage": {"input_tokens": 49093, "output_tokens": 306, "cache_read_input_tokens": 195584,
               "cache_creation_input_tokens": 0}, "total_cost_usd": 0.746, "num_turns": 3,
     "model_usage": {"glm-5.1": {}}},
    {"usage": {"input_tokens": 5651, "output_tokens": 304, "cache_read_input_tokens": 256832,
               "cache_creation_input_tokens": 0}, "total_cost_usd": 1.005, "num_turns": 3},
    {"usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
               "cache_creation_input_tokens": 0}, "total_cost_usd": 1.005, "num_turns": 1},
]


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    ptt.ensure_token_usage_events_table(conn)
    return conn


def test_sum_turn_usage_sums_per_iteration_not_tail():
    agg = ptt.sum_turn_usage(_DELTA)
    # SUM, not tail. Tail would have been ~0 (the trailing no-op message).
    assert agg["input_tokens"] == 49093 + 5651
    assert agg["output_tokens"] == 306 + 304
    assert agg["cache_read_input_tokens"] == 195584 + 256832
    assert agg["total_tokens"] == (49093 + 5651) + (306 + 304) + (195584 + 256832)
    assert agg["num_turns"] == 3
    assert agg["model"] == "glm-5.1"
    assert agg["last_cost"] == pytest.approx(1.005)
    assert agg["status"] == "ok"


def test_sum_turn_usage_flags_error():
    agg = ptt.sum_turn_usage([{"usage": {"input_tokens": 5}, "is_error": True}])
    assert agg["status"] == "error"


def test_record_inserts_one_row_with_summed_tokens_and_cost_delta():
    conn = _mem_conn()
    rowid = ptt.record_session_token_usage(
        delta_messages=_DELTA, run_source="heartbeat",
        baseline_cost_usd=0.5, session_id="s1", run_id="r1", conn=conn,
    )
    assert rowid is not None
    row = conn.execute(
        "SELECT source, principal, input_tokens, output_tokens, "
        "cache_read_input_tokens, total_cost_usd, num_turns, session_id, run_id, model "
        "FROM token_usage_events"
    ).fetchone()
    assert row[0] == "cli-in-process"
    assert row[1] == "simone"
    assert row[2] == 49093 + 5651
    assert row[3] == 306 + 304
    assert row[4] == 195584 + 256832
    # cost = last cumulative (1.005) - baseline (0.5)
    assert row[5] == pytest.approx(1.005 - 0.5)
    assert row[6] == 3
    assert row[7] == "s1"
    assert row[8] == "r1"
    assert row[9] == "glm-5.1"


def test_record_no_op_when_zero_tokens_returns_none():
    conn = _mem_conn()
    rowid = ptt.record_session_token_usage(
        delta_messages=[{"usage": {"input_tokens": 0, "output_tokens": 0}}],
        run_source="heartbeat", conn=conn,
    )
    assert rowid is None
    assert conn.execute("SELECT COUNT(*) FROM token_usage_events").fetchone()[0] == 0


def test_record_no_op_on_empty_delta():
    conn = _mem_conn()
    assert ptt.record_session_token_usage(delta_messages=[], run_source="heartbeat", conn=conn) is None


def test_record_disabled_by_env(monkeypatch):
    monkeypatch.setenv("UA_TOKEN_SINK_ENABLED", "0")
    conn = _mem_conn()
    assert ptt.record_session_token_usage(delta_messages=_DELTA, run_source="heartbeat", conn=conn) is None
    assert conn.execute("SELECT COUNT(*) FROM token_usage_events").fetchone()[0] == 0


@pytest.mark.parametrize("run_source,expected", [
    ("heartbeat", ("cli-in-process", "simone")),
    ("cron", ("cli-in-process", "simone")),
    ("vp.coder.external", ("cli-in-process", "vp-coder")),
    ("vp.coder", ("cli-in-process", "vp-coder")),
    ("vp.general.primary", ("cli-in-process", "vp")),
    ("user", ("cli-in-process", "interactive")),
    ("webhook", ("cli-in-process", "interactive")),
    ("", ("cli-in-process", "interactive")),
])
def test_classify_run_source(run_source, expected):
    assert ptt.classify_run_source(run_source) == expected


def test_record_never_raises_on_broken_conn():
    class _Boom:
        def execute(self, *a, **k):
            raise RuntimeError("boom")

    # Must swallow and return None — a telemetry failure can never abort a turn.
    assert ptt.record_session_token_usage(
        delta_messages=_DELTA, run_source="heartbeat", conn=_Boom()
    ) is None


def test_record_self_heals_missing_table():
    conn = sqlite3.connect(":memory:")  # NO table created
    rowid = ptt.record_session_token_usage(
        delta_messages=_DELTA, run_source="heartbeat", conn=conn,
    )
    assert rowid is not None
    assert conn.execute("SELECT COUNT(*) FROM token_usage_events").fetchone()[0] == 1


def test_cost_clamped_non_negative_when_baseline_exceeds():
    conn = _mem_conn()
    ptt.record_session_token_usage(
        delta_messages=_DELTA, run_source="heartbeat", baseline_cost_usd=99.0, conn=conn,
    )
    cost = conn.execute("SELECT total_cost_usd FROM token_usage_events").fetchone()[0]
    assert cost == 0.0


# ── slice_turn_delta: the load-bearing main.py double-count guard (factored out) ──

def test_slice_turn_delta_clamps_bad_index():
    msgs = [{"total_cost_usd": 1.0}]
    # Bad indices clamp to "no delta" — an empty delta means no row is recorded,
    # so the baseline value is irrelevant in those cases.
    assert ptt.slice_turn_delta(msgs, -1)[0] == []         # negative → no delta
    assert ptt.slice_turn_delta(msgs, 99)[0] == []         # past end → no delta
    assert ptt.slice_turn_delta([], 0) == ([], 0.0)
    assert ptt.slice_turn_delta(None, 0) == ([], 0.0)


def test_slice_turn_delta_disjoint_across_reused_trace():
    # Simulates main.py: ONE sdk_result_messages list reused across two turns.
    msgs: list[dict] = []
    # turn 1 entry
    start1 = len(msgs)
    msgs += [
        {"usage": {"input_tokens": 100}, "total_cost_usd": 0.5},
        {"usage": {"input_tokens": 200}, "total_cost_usd": 0.9},
    ]
    delta1, base1 = ptt.slice_turn_delta(msgs, start1)
    # turn 2 entry (list reused, keeps growing)
    start2 = len(msgs)
    msgs += [
        {"usage": {"input_tokens": 30}, "total_cost_usd": 1.4},
    ]
    delta2, base2 = ptt.slice_turn_delta(msgs, start2)

    # Disjoint slices — no message appears in both turns.
    assert [m["usage"]["input_tokens"] for m in delta1] == [100, 200]
    assert [m["usage"]["input_tokens"] for m in delta2] == [30]
    assert base1 == 0.0                 # no prior turn
    assert base2 == pytest.approx(0.9)  # cumulative cost just before turn 2

    # End-to-end: each turn records its OWN tokens and its OWN cost-delta.
    conn = _mem_conn()
    ptt.record_session_token_usage(delta_messages=delta1, run_source="heartbeat",
                                   baseline_cost_usd=base1, conn=conn)
    ptt.record_session_token_usage(delta_messages=delta2, run_source="heartbeat",
                                   baseline_cost_usd=base2, conn=conn)
    rows = conn.execute(
        "SELECT input_tokens, total_cost_usd FROM token_usage_events ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] == 300 and rows[0][1] == pytest.approx(0.9)        # turn 1
    assert rows[1][0] == 30 and rows[1][1] == pytest.approx(1.4 - 0.9)   # turn 2 delta
