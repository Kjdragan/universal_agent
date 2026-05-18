"""Hermes Phase E.2b — Cody token tracking unit tests.

Covers the schema + accumulator service + endpoint shape used by the
dashboard tile:

* `cody_token_usage` table round-trips.
* `record_token_usage` extracts each known cost_info field correctly
  and is best-effort (never raises on bad input).
* `summarize_window` filters by `recorded_at >= reset_at` cursor and
  by `cody_mode`, and rolls up `model_breakdown`.
* `reset_window` bumps the cursor; new rows under the cursor are
  excluded from totals; pre-reset rows remain in the DB for forensics.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services.cody_token_tracking import (
    get_window_state,
    record_token_usage,
    reset_window,
    summarize_window,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    task_hub.ensure_schema(c)
    yield c
    c.close()


def _backdate_last_row(conn: sqlite3.Connection, iso: str) -> None:
    """Helper: rewrite the most-recent row's recorded_at for window tests."""
    conn.execute(
        "UPDATE cody_token_usage SET recorded_at = ? WHERE id = (SELECT MAX(id) FROM cody_token_usage)",
        (iso,),
    )
    conn.commit()


# ── Schema ────────────────────────────────────────────────────────────────


def test_cody_token_usage_table_exists(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(cody_token_usage)")}
    assert {
        "id",
        "mission_id",
        "task_id",
        "cody_mode",
        "model",
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "total_cost_usd",
        "duration_ms",
        "recorded_at",
    } <= cols


# ── record_token_usage ────────────────────────────────────────────────────


def test_record_token_usage_persists_all_fields(conn: sqlite3.Connection) -> None:
    row_id = record_token_usage(
        conn,
        cody_mode="anthropic",
        mission_id="vp-mission-abc",
        task_id="task:x",
        model="claude-opus-4-7",
        cost_info={
            "input_tokens": 100,
            "output_tokens": 250,
            "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 30,
            "total_cost_usd": 0.0512,
            "duration_ms": 1234,
        },
    )
    assert isinstance(row_id, int)
    row = conn.execute("SELECT * FROM cody_token_usage WHERE id = ?", (row_id,)).fetchone()
    assert row["cody_mode"] == "anthropic"
    assert row["mission_id"] == "vp-mission-abc"
    assert row["task_id"] == "task:x"
    assert row["model"] == "claude-opus-4-7"
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 250
    assert row["cache_creation_input_tokens"] == 20
    assert row["cache_read_input_tokens"] == 30
    assert row["total_cost_usd"] == pytest.approx(0.0512)
    assert row["duration_ms"] == 1234
    assert row["recorded_at"]  # non-empty


def test_record_token_usage_accepts_cost_usd_alias(conn: sqlite3.Connection) -> None:
    """Some stream-json variants use `cost_usd` instead of `total_cost_usd`."""
    record_token_usage(
        conn,
        cody_mode="anthropic",
        cost_info={"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001},
    )
    row = conn.execute("SELECT total_cost_usd FROM cody_token_usage LIMIT 1").fetchone()
    assert row["total_cost_usd"] == pytest.approx(0.001)


def test_record_token_usage_rejects_unknown_mode(conn: sqlite3.Connection) -> None:
    out = record_token_usage(conn, cody_mode="bogus", cost_info={"input_tokens": 5})
    assert out is None
    count = conn.execute("SELECT COUNT(*) FROM cody_token_usage").fetchone()[0]
    assert count == 0


def test_record_token_usage_handles_missing_cost_info(conn: sqlite3.Connection) -> None:
    """Empty cost_info should record a zero-token row, not raise."""
    row_id = record_token_usage(conn, cody_mode="zai", cost_info=None)
    assert isinstance(row_id, int)
    row = conn.execute("SELECT * FROM cody_token_usage WHERE id = ?", (row_id,)).fetchone()
    assert row["input_tokens"] == 0
    assert row["output_tokens"] == 0
    assert row["total_cost_usd"] == 0.0


def test_record_token_usage_handles_garbage_values(conn: sqlite3.Connection) -> None:
    """Non-numeric values fall through to zeros."""
    row_id = record_token_usage(
        conn,
        cody_mode="anthropic",
        cost_info={"input_tokens": "not-a-number", "output_tokens": None, "total_cost_usd": "garbage"},
    )
    assert isinstance(row_id, int)
    row = conn.execute("SELECT * FROM cody_token_usage WHERE id = ?", (row_id,)).fetchone()
    assert row["input_tokens"] == 0
    assert row["output_tokens"] == 0
    assert row["total_cost_usd"] == 0.0


# ── window + summarize ────────────────────────────────────────────────────


def test_summarize_window_with_no_reset_uses_epoch_cursor(conn: sqlite3.Connection) -> None:
    """No prior reset → cursor is 1970, so all rows are in-window."""
    record_token_usage(
        conn,
        cody_mode="anthropic",
        cost_info={"input_tokens": 100, "output_tokens": 200, "total_cost_usd": 0.05},
    )
    summary = summarize_window(conn, cody_mode="anthropic")
    assert summary["mission_count"] == 1
    assert summary["input_tokens"] == 100
    assert summary["output_tokens"] == 200
    assert summary["total_cost_usd"] == pytest.approx(0.05)
    assert summary["cody_mode_filter"] == "anthropic"
    assert summary["reset_at"].startswith("1970-")


def test_summarize_window_filters_by_cody_mode(conn: sqlite3.Connection) -> None:
    """Anthropic-only filter excludes ZAI rows."""
    record_token_usage(conn, cody_mode="anthropic", cost_info={"input_tokens": 100})
    record_token_usage(conn, cody_mode="zai", cost_info={"input_tokens": 999})
    anth = summarize_window(conn, cody_mode="anthropic")
    zai = summarize_window(conn, cody_mode="zai")
    all_mode = summarize_window(conn, cody_mode=None)
    assert anth["input_tokens"] == 100
    assert zai["input_tokens"] == 999
    assert all_mode["input_tokens"] == 1099


def test_reset_window_excludes_pre_reset_rows(conn: sqlite3.Connection) -> None:
    """Rows recorded before reset_at are excluded from totals (history kept)."""
    record_token_usage(conn, cody_mode="anthropic", cost_info={"input_tokens": 500})
    # Backdate it so the upcoming reset puts it pre-cursor.
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    _backdate_last_row(conn, old)
    # Reset cursor to a time after the backdated row.
    reset_window(conn, reset_by="test-operator")
    # New post-reset row.
    record_token_usage(conn, cody_mode="anthropic", cost_info={"input_tokens": 7})
    summary = summarize_window(conn, cody_mode="anthropic")
    assert summary["mission_count"] == 1
    assert summary["input_tokens"] == 7
    # But raw history is preserved.
    total_rows = conn.execute("SELECT COUNT(*) FROM cody_token_usage").fetchone()[0]
    assert total_rows == 2
    state = get_window_state(conn)
    assert state["reset_by"] == "test-operator"


def test_summarize_window_model_breakdown(conn: sqlite3.Connection) -> None:
    """Model breakdown groups by model name with per-model totals."""
    record_token_usage(
        conn,
        cody_mode="anthropic",
        model="claude-opus-4-7",
        cost_info={"input_tokens": 100, "output_tokens": 50, "total_cost_usd": 0.02},
    )
    record_token_usage(
        conn,
        cody_mode="anthropic",
        model="claude-opus-4-7",
        cost_info={"input_tokens": 100, "output_tokens": 50, "total_cost_usd": 0.02},
    )
    record_token_usage(
        conn,
        cody_mode="anthropic",
        model="claude-sonnet-4-6",
        cost_info={"input_tokens": 200, "output_tokens": 100, "total_cost_usd": 0.01},
    )
    summary = summarize_window(conn, cody_mode="anthropic")
    bd = {r["model"]: r for r in summary["model_breakdown"]}
    assert bd["claude-opus-4-7"]["missions"] == 2
    assert bd["claude-opus-4-7"]["input_tokens"] == 200
    assert bd["claude-sonnet-4-6"]["missions"] == 1
    assert bd["claude-sonnet-4-6"]["input_tokens"] == 200


def test_summarize_window_days_in_window(conn: sqlite3.Connection) -> None:
    """days_in_window is a float number of days since reset_at."""
    summary = summarize_window(conn, cody_mode="anthropic")
    # With no reset, cursor is epoch → many years.
    assert summary["days_in_window"] > 365 * 30


# ── Endpoint smoke ────────────────────────────────────────────────────────


def test_get_endpoint_returns_summary(monkeypatch, tmp_path) -> None:
    """Smoke test the API handler shape (no full FastAPI lifespan)."""
    db_path = str(tmp_path / "activity.db")
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", db_path)
    # Schema seed.
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    task_hub.ensure_schema(c)
    record_token_usage(c, cody_mode="anthropic", cost_info={"input_tokens": 42, "output_tokens": 12, "total_cost_usd": 0.005})
    c.close()

    from universal_agent.gateway_server import cody_anthropic_token_tracking_get

    out = asyncio.run(cody_anthropic_token_tracking_get(mode="anthropic"))
    assert out["mission_count"] == 1
    assert out["input_tokens"] == 42
    assert out["output_tokens"] == 12
    assert out["cody_mode_filter"] == "anthropic"


def test_get_endpoint_invalid_mode(monkeypatch, tmp_path) -> None:
    db_path = str(tmp_path / "activity.db")
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", db_path)
    c = sqlite3.connect(db_path)
    task_hub.ensure_schema(c)
    c.close()

    from fastapi import HTTPException

    from universal_agent.gateway_server import cody_anthropic_token_tracking_get

    with pytest.raises(HTTPException) as exc:
        asyncio.run(cody_anthropic_token_tracking_get(mode="garbage"))
    assert exc.value.status_code == 400
