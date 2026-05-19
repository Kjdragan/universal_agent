"""Tests for the gateway startup pre-warm helper.

When the gateway restarts on a merge-to-main deploy, the first request to
each dashboard panel pays the full SQLite cold-page + connection-setup cost.
Observed live tonight: ``/api/v1/dashboard/proactive-signals`` took 12.9s on
the first hit after restart, while the steady-state response is <100ms.

The pre-warm helper fires representative queries against the activity DB at
startup so subsequent operator dashboard loads find a warm page cache.
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent.services.dashboard_prewarm import prewarm_dashboard_db


@pytest.fixture
def in_memory_conn() -> sqlite3.Connection:
    """An in-memory SQLite with the minimum schema the pre-warm queries touch."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE task_hub_items (
            task_id TEXT PRIMARY KEY,
            source_kind TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE proactive_signal_cards (
            card_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def test_prewarm_dashboard_db_returns_summary(in_memory_conn: sqlite3.Connection) -> None:
    """Slice 1 (tracer bullet) — function exists, runs, reports success + duration."""

    summary = prewarm_dashboard_db(lambda: in_memory_conn)

    assert summary["db_warmed"] is True
    assert isinstance(summary["duration_ms"], (int, float))
    assert summary["duration_ms"] >= 0


def test_prewarm_dashboard_db_survives_connect_failure() -> None:
    """Slice 3a — connect() raising must not propagate to the gateway lifespan.

    A broken DB at startup is bad, but it's strictly worse if the gateway
    itself fails to come up. Pre-warm is best-effort by design.
    """

    def broken_connect() -> sqlite3.Connection:
        raise sqlite3.OperationalError("unable to open database file")

    summary = prewarm_dashboard_db(broken_connect)

    assert summary["db_warmed"] is False
    assert "error" in summary
    assert "unable to open" in summary["error"]
    assert summary["tables_warmed"] == []


def test_prewarm_dashboard_db_survives_missing_table() -> None:
    """Slice 3b — a missing hot table is reported, not raised.

    Real failure mode this catches: a schema migration in flight or a partial
    DB where one table doesn't exist yet. Warm what we can; don't block boot.
    """
    conn = sqlite3.connect(":memory:")
    # task_hub_items only — proactive_signal_cards missing on purpose.
    conn.execute(
        "CREATE TABLE task_hub_items (task_id TEXT PRIMARY KEY, source_kind TEXT, "
        "status TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.commit()

    summary = prewarm_dashboard_db(lambda: conn)

    assert summary["db_warmed"] is True
    assert summary["tables_warmed"] == ["task_hub_items"]
    assert "tables_failed" in summary
    assert "proactive_signal_cards" in summary["tables_failed"]


def test_prewarm_dashboard_db_warms_hot_tables(in_memory_conn: sqlite3.Connection) -> None:
    """Slice 2 — both dashboard hot paths get a row touched so their pages are cached.

    The two SLOW endpoints under cold start are ``/dashboard/proactive-signals``
    (reads ``proactive_signal_cards``) and the Task Hub queue (reads
    ``task_hub_items``). After warmup the summary should report both tables
    were warmed.
    """
    # Seed one row in each so warming has something to read.
    in_memory_conn.execute(
        "INSERT INTO task_hub_items VALUES "
        "('t1', 'proactive_signal', 'open', '2026-05-19T00:00:00Z', '2026-05-19T00:00:00Z')"
    )
    in_memory_conn.execute(
        "INSERT INTO proactive_signal_cards VALUES "
        "('c1', 'youtube', 'pending', '2026-05-19T00:00:00Z')"
    )
    in_memory_conn.commit()

    summary = prewarm_dashboard_db(lambda: in_memory_conn)

    assert summary["db_warmed"] is True
    assert "tables_warmed" in summary
    assert set(summary["tables_warmed"]) == {"task_hub_items", "proactive_signal_cards"}
