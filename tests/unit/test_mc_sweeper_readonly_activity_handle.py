"""Mission Control sweeper — read-only activity/Task-Hub handle guarantee.

The sweeper is observational: every write it performs goes to the separate
Mission Control store (`open_store()`), and it only ever SELECTs from the
activity / Task-Hub DB (`tile.compute_state`, `collect_tier1_evidence`).

`MissionControlSweeper._open_activity_db` therefore opens that handle with
SQLite's URI `mode=ro` so the observational guarantee is enforced by the
driver, not by convention. These tests pin:

  - the sweeper-opened activity connection REJECTS writes
    (INSERT / CREATE raise `sqlite3.OperationalError` matching "readonly")
    while SELECTs still work, and
  - `collect_tier1_evidence(..., activity_read_only=True)` does not attempt
    the Task-Hub-missions schema-bootstrap DDL on the read-only handle even
    when `UA_TASK_HUB_MISSIONS_ENABLED=1` (so the ro switch is safe
    regardless of that feature flag).
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from universal_agent.services.mission_control_db import open_store
from universal_agent.services.mission_control_intelligence_sweeper import (
    MissionControlSweeper,
)
from universal_agent.services.mission_control_tier1 import collect_tier1_evidence


@pytest.fixture
def activity_db_path(tmp_path: Path, monkeypatch) -> Path:
    """A real on-disk activity DB with the minimal Task-Hub shape, with
    UA_ACTIVITY_DB_PATH pointed at it so the *default* `_open_activity_db`
    (not a fixture override) resolves to this file.
    """
    path = tmp_path / "activity_state.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE task_hub_items (
            task_id TEXT PRIMARY KEY,
            source_kind TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            priority INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE activity_events (
            id TEXT PRIMARY KEY,
            source_domain TEXT NOT NULL,
            severity TEXT,
            requires_action INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO task_hub_items (task_id, source_kind, title, status, "
        "created_at, updated_at) VALUES ('t1','manual','seed','open','x','x')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(path))
    return path


def test_sweeper_activity_handle_allows_reads(activity_db_path):
    """Sanity: the read-only handle can still SELECT existing rows."""
    conn = MissionControlSweeper()._open_activity_db()
    try:
        rows = conn.execute("SELECT task_id FROM task_hub_items").fetchall()
        assert [r["task_id"] for r in rows] == ["t1"]
    finally:
        conn.close()


def test_sweeper_activity_handle_rejects_insert(activity_db_path):
    """A write through the sweeper handle must raise readonly, not mutate."""
    conn = MissionControlSweeper()._open_activity_db()
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute(
                "INSERT INTO task_hub_items (task_id, source_kind, title, "
                "status, created_at, updated_at) "
                "VALUES ('t2','manual','blocked','open','x','x')"
            )
    finally:
        conn.close()

    # Prove the row never landed (open a fresh writable handle to check).
    verify = sqlite3.connect(str(activity_db_path))
    try:
        count = verify.execute(
            "SELECT COUNT(*) FROM task_hub_items"
        ).fetchone()[0]
        assert count == 1, "read-only handle must not have written row t2"
    finally:
        verify.close()


def test_sweeper_activity_handle_rejects_ddl(activity_db_path):
    """Schema DDL (the missions-block ensure_schema case) must raise readonly."""
    conn = MissionControlSweeper()._open_activity_db()
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE should_not_exist (x INTEGER)")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE INDEX ix_t1 ON task_hub_items (status)")
    finally:
        conn.close()


def test_collect_tier1_evidence_skips_missions_ddl_on_ro_handle(
    activity_db_path, tmp_path, monkeypatch
):
    """Even with missions enabled, `activity_read_only=True` must NOT attempt
    the schema-bootstrap DDL on the read-only handle — it skips the block
    cleanly instead of raising or relying on a swallowed OperationalError.
    """
    monkeypatch.setenv("UA_TASK_HUB_MISSIONS_ENABLED", "1")
    activity_conn = MissionControlSweeper()._open_activity_db()
    mc_conn = open_store(tmp_path / "mc.db")
    try:
        evidence = collect_tier1_evidence(
            activity_conn, mc_conn, activity_read_only=True
        )
    finally:
        mc_conn.close()
        activity_conn.close()

    # Missions block was skipped (not attempted) on the read-only handle.
    assert evidence["mission_summaries"] == []
    assert evidence.get("mission_summaries_skipped") == "activity_conn_read_only"
    # The non-missions evidence still collected fine over the ro handle.
    assert evidence["active_or_attention_tasks"], "expected the seeded task row"
