"""Unit tests for the consolidated multi-lane token reader (Phase 3).

Proves the dashboard now sees EVERY token lane — not just the httpx slice the old
panel saw — with cache-inclusive totals, legacy aliases preserved, and a per-day
trend. Drives real temp SQLite DBs (activity_state.db + csi.db); no LLM, no network.
"""

from __future__ import annotations

import sqlite3
import time

import pytest


@pytest.fixture
def activity_db(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "activity.db"))
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.task_hub import ensure_schema

    conn = connect_runtime_db(get_activity_db_path())
    ensure_schema(conn)
    return conn


def _now() -> float:
    return time.time()


def _insert_sink(conn, *, principal, model, input_t, output_t, cache_read, ts, status="ok", num_turns=1):
    conn.execute(
        """INSERT INTO token_usage_events
           (ts, recorded_at, source, principal, model, caller, caller_fn, status,
            input_tokens, output_tokens, cache_creation_input_tokens,
            cache_read_input_tokens, total_cost_usd, num_turns)
           VALUES (?,?, 'cli-in-process', ?,?,?,?,?, ?,?,0,?, 0.5, ?)""",
        (ts, "2026-06-14T00:00:00", principal, model, principal, f"{principal}::turn",
         status, input_t, output_t, cache_read, num_turns),
    )
    conn.commit()


def test_sink_groups_by_principal_cache_inclusive(activity_db):
    now = _now()
    _insert_sink(activity_db, principal="simone", model="glm-5.1",
                 input_t=200_000, output_t=18_000, cache_read=1_740_000, ts=now - 100)
    _insert_sink(activity_db, principal="vp", model="glm-5.1",
                 input_t=180_000, output_t=10_000, cache_read=552_000, ts=now - 200)

    from universal_agent.services.token_consolidation import analyze_sink_token_usage

    src = analyze_sink_token_usage(now, 3600)
    assert src["available"] is True
    by = {p["caller"]: p for p in src["processes"]}
    assert set(by) == {"simone", "vp"}
    # cache-inclusive total dominates (cache_read is the bulk)
    assert by["simone"]["total_tokens"] == 200_000 + 18_000 + 1_740_000
    assert by["simone"]["cache_read_input_tokens"] == 1_740_000
    # source total is cache-inclusive and ranks simone first
    assert src["totals"]["total_tokens"] == (
        200_000 + 18_000 + 1_740_000 + 180_000 + 10_000 + 552_000
    )
    assert src["processes"][0]["caller"] == "simone"


def test_sink_window_excludes_old_rows(activity_db):
    now = _now()
    _insert_sink(activity_db, principal="simone", model="glm-5.1",
                 input_t=100, output_t=10, cache_read=1000, ts=now - 10)
    _insert_sink(activity_db, principal="simone", model="glm-5.1",
                 input_t=999, output_t=99, cache_read=9999, ts=now - 100_000)  # outside 1h

    from universal_agent.services.token_consolidation import analyze_sink_token_usage

    src = analyze_sink_token_usage(now, 3600)
    assert src["totals"]["total_tokens"] == 1110  # only the in-window row


def test_csi_reader_failsoft_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_DB_PATH", str(tmp_path / "nope.db"))
    from universal_agent.services.token_consolidation import read_csi_token_usage

    src = read_csi_token_usage(_now(), 3600)
    assert src["available"] is False
    assert src["processes"] == []


def test_csi_reader_reads_rows(tmp_path, monkeypatch):
    path = tmp_path / "csi.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT, process_name TEXT,
            model_name TEXT, prompt_tokens INT, completion_tokens INT, total_tokens INT,
            metadata_json TEXT, created_at TEXT)"""
    )
    import datetime

    recent = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO token_usage (occurred_at, process_name, model_name, prompt_tokens, completion_tokens, total_tokens) VALUES (?,?,?,?,?,?)",
        (recent, "batch_brief_claude", "glm-4.5-air", 5000, 800, 5800),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CSI_DB_PATH", str(path))

    from universal_agent.services.token_consolidation import read_csi_token_usage

    src = read_csi_token_usage(_now(), 3600)
    assert src["available"] is True
    assert src["processes"][0]["caller"] == "csi:batch_brief_claude"
    assert src["processes"][0]["total_tokens"] == 5800


def test_demo_factory_reader_failsoft_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "UA_DEMO_FACTORY_TOKEN_LEDGER_PATH", str(tmp_path / "nope.jsonl")
    )
    from universal_agent.services.token_consolidation import (
        read_demo_factory_token_usage,
    )

    src = read_demo_factory_token_usage(_now(), 3600)
    assert src["available"] is False
    assert src["processes"] == []


def test_demo_factory_reader_counts_only_zai_rows(tmp_path, monkeypatch):
    import json as _json

    now = _now()
    path = tmp_path / "token_usage.jsonl"
    rows = [
        # counted: explicit zai provider
        {"ts": now - 30, "provider": "zai", "model": "glm-5-turbo",
         "caller": "goal_build", "input_tokens": 1000, "output_tokens": 200,
         "cache_read_input_tokens": 9000},
        # counted: glm model id even without provider tag
        {"ts": now - 20, "model": "glm-4.6", "caller": "ledger_weekly",
         "input_tokens": 500, "output_tokens": 100},
        # NOT counted: Anthropic row must never inflate the ZAI lane
        {"ts": now - 10, "provider": "anthropic", "model": "claude-opus-5",
         "caller": "goal_build", "input_tokens": 999_999, "output_tokens": 999},
        # NOT counted: outside the window
        {"ts": now - 999_999, "provider": "zai", "model": "glm-5-turbo",
         "caller": "goal_build", "input_tokens": 777},
        # NOT counted: garbage line survives without breaking the reader
    ]
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(_json.dumps(r) + "\n")
        fh.write("not json at all\n")
    monkeypatch.setenv("UA_DEMO_FACTORY_TOKEN_LEDGER_PATH", str(path))

    from universal_agent.services.token_consolidation import (
        read_demo_factory_token_usage,
    )

    src = read_demo_factory_token_usage(now, 3600)
    assert src["available"] is True
    assert src["token_events_seen"] == 2
    # cache-inclusive: 1000+200+9000 + 500+100
    assert src["totals"]["total_tokens"] == 10_800
    callers = {p["caller"] for p in src["processes"]}
    assert callers == {"demo_factory:goal_build", "demo_factory:ledger_weekly"}
    top = src["processes"][0]
    assert top["caller"] == "demo_factory:goal_build"
    assert top["total_tokens"] == 10_200


def test_build_token_usage_legacy_aliases_equal_consolidated(activity_db, tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_DB_PATH", str(tmp_path / "absent.db"))  # csi fail-soft
    monkeypatch.setenv(
        "UA_DEMO_FACTORY_TOKEN_LEDGER_PATH", str(tmp_path / "absent.jsonl")
    )  # demo-factory fail-soft
    now = _now()
    _insert_sink(activity_db, principal="simone", model="glm-5.1",
                 input_t=200_000, output_t=18_000, cache_read=1_740_000, ts=now - 50)

    from universal_agent.services.zai_status import build_token_usage

    out = build_token_usage(3600)
    assert out["available"] is True
    # sources[] present with the in-process lane carrying the simone spend
    src_names = {s["source"] for s in out["sources"]}
    assert {
        "httpx-zai",
        "cli-in-process",
        "cli-subprocess",
        "csi-ingester",
        "demo-factory",
    } <= src_names
    # MANDATORY: legacy aliases == consolidated (backend-ahead-of-UI safety)
    assert out["totals"] == out["consolidated"]["totals"]
    assert out["processes"] == out["consolidated"]["processes"]
    assert out["token_events_seen"] == out["consolidated"]["token_events_seen"]
    # the previously-invisible simone spend now surfaces in the consolidated total
    assert out["consolidated"]["totals"]["total_tokens"] >= 1_958_000
    # trend present
    assert "buckets" in out["trend"] and "series" in out["trend"]


def test_trend_buckets_by_day(activity_db):
    now = _now()
    _insert_sink(activity_db, principal="simone", model="glm-5.1",
                 input_t=100, output_t=10, cache_read=2000, ts=now - 50)
    from universal_agent.services.token_consolidation import build_trend

    trend = build_trend(now, 6 * 86400)
    assert len(trend["buckets"]) >= 1
    simone = [s for s in trend["series"] if s["key"] == "simone"]
    assert simone, "simone should appear in the trend series"
    assert sum(simone[0]["tokens"]) == 2110
