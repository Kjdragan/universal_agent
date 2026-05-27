"""Regression: ensure_schema upgrade path from pre-priority-tier DB.

The 2026-05-27 18:32 UTC production deploy crashlooped the gateway with:

    File "src/universal_agent/durable/migrations.py", line 301, in ensure_schema
        conn.executescript(SCHEMA_SQL)
    sqlite3.OperationalError: no such column: priority_tier

Root cause: the new index ``idx_vp_missions_tier_priority`` was declared
inside ``SCHEMA_SQL`` and referenced ``priority_tier``. On a fresh DB
the CREATE TABLE in SCHEMA_SQL builds the column, so the index works.
But on production the table already existed from a prior schema —
``CREATE TABLE IF NOT EXISTS`` is a no-op, ``priority_tier`` doesn't
exist yet, and ``executescript`` fails before ``_add_column_if_missing``
gets a chance to ALTER it on.

Fix: move the index creation OUT of SCHEMA_SQL and into ``ensure_schema``
AFTER the ALTER TABLE call. This test pins that ordering by simulating
the exact upgrade shape that crashed production.
"""
from __future__ import annotations

import sqlite3

import pytest

from universal_agent.durable.migrations import ensure_schema


def _create_pre_tier_vp_missions(conn: sqlite3.Connection) -> None:
    """Build the vp_missions table in its pre-priority_tier shape.

    Mirrors the production schema circa 2026-05-26 exactly: same
    columns, same DEFAULT, NO priority_tier. Anything else and the
    regression doesn't reproduce the deploy crash.
    """
    conn.execute(
        """
        CREATE TABLE vp_sessions (
          vp_id TEXT PRIMARY KEY,
          runtime_id TEXT NOT NULL,
          session_id TEXT,
          workspace_dir TEXT,
          status TEXT NOT NULL,
          lease_owner TEXT,
          lease_expires_at TEXT,
          last_heartbeat_at TEXT,
          last_error TEXT,
          metadata_json TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE vp_missions (
          mission_id TEXT PRIMARY KEY,
          vp_id TEXT NOT NULL,
          run_id TEXT,
          status TEXT NOT NULL,
          mission_type TEXT,
          objective TEXT NOT NULL,
          budget_json TEXT,
          payload_json TEXT,
          result_ref TEXT,
          priority INTEGER DEFAULT 100,
          worker_id TEXT,
          claim_expires_at TEXT,
          cancel_requested INTEGER DEFAULT 0,
          created_at TEXT NOT NULL,
          started_at TEXT,
          completed_at TEXT,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(vp_id) REFERENCES vp_sessions(vp_id)
        )
        """
    )
    conn.commit()


def test_ensure_schema_succeeds_on_pre_tier_vp_missions_table():
    """On the production crash shape, ensure_schema must not raise."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_pre_tier_vp_missions(conn)

    # Sanity: confirm the seeded table lacks priority_tier (otherwise
    # the regression isn't reproducing the production state).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(vp_missions)").fetchall()}
    assert "priority_tier" not in cols, "seed leaked priority_tier"

    # This is the call that crashed production. Must NOT raise.
    ensure_schema(conn)

    # After ensure_schema, priority_tier exists with the safe default.
    cols_after = {
        r[1]: r for r in conn.execute("PRAGMA table_info(vp_missions)").fetchall()
    }
    assert "priority_tier" in cols_after

    # And the index now exists.
    indexes = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='vp_missions'"
        ).fetchall()
    }
    assert "idx_vp_missions_tier_priority" in indexes


def test_ensure_schema_is_idempotent_on_pre_tier_then_fresh_run():
    """Calling ensure_schema twice must remain a no-op on the second call."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_pre_tier_vp_missions(conn)

    ensure_schema(conn)
    # Second call should NOT raise (index uses IF NOT EXISTS; ALTER
    # only fires when column missing).
    ensure_schema(conn)

    # Insert a pre-tier row so backfill has something to update on
    # subsequent calls, and verify backfill is also idempotent.
    conn.execute(
        """
        INSERT INTO vp_sessions
          (vp_id, runtime_id, status, created_at, updated_at)
        VALUES ('vp.general.primary', 'rt.test', 'idle',
                '2026-05-27T00:00:00Z', '2026-05-27T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO vp_missions
          (mission_id, vp_id, status, mission_type, objective,
           created_at, updated_at, priority_tier)
        VALUES ('m1', 'vp.general.primary', 'queued', 'briefing',
                'test', '2026-05-27T00:00:00Z', '2026-05-27T00:00:00Z',
                'background')
        """
    )
    conn.commit()

    ensure_schema(conn)  # idempotent third call after data

    # Backfill ran: a 'briefing' row should now be operator_daily,
    # not background.
    row = conn.execute(
        "SELECT priority_tier FROM vp_missions WHERE mission_id = 'm1'"
    ).fetchone()
    assert row["priority_tier"] == "operator_daily"
