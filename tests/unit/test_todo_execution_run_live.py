"""The split false-orphan guard: reconcile must not reap a live worker-hosted
todo_execution row. _todo_execution_run_live is strictly additive — it can only
SUPPRESS a reap (return True), never cause one."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import universal_agent.task_hub as th

_NOW = datetime(2026, 6, 19, 20, 0, 0, tzinfo=timezone.utc)
_NOW_ISO = _NOW.isoformat()


def _make_runs_db(path, rows):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE runs (run_id TEXT, status TEXT, last_heartbeat_at TEXT, "
        "updated_at TEXT, created_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO runs (run_id, status, last_heartbeat_at, updated_at, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _point_at(monkeypatch, path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(path))
    monkeypatch.delenv("UA_RECONCILE_TODO_RUN_GUARD_ENABLED", raising=False)


def test_running_and_fresh_run_is_live(monkeypatch, tmp_path):
    db = tmp_path / "runtime.db"
    fresh = (_NOW - timedelta(minutes=5)).isoformat()
    _make_runs_db(db, [("run_live", "running", None, None, fresh)])
    _point_at(monkeypatch, db)
    assert th._todo_execution_run_live({"workflow_run_id": "run_live"}, {}, _NOW_ISO) is True


def test_running_but_stale_is_not_live(monkeypatch, tmp_path):
    db = tmp_path / "runtime.db"
    stale = (_NOW - timedelta(minutes=40)).isoformat()  # > 35-min window
    _make_runs_db(db, [("run_stale", "running", None, None, stale)])
    _point_at(monkeypatch, db)
    assert th._todo_execution_run_live({"workflow_run_id": "run_stale"}, {}, _NOW_ISO) is False


def test_terminal_run_is_not_live(monkeypatch, tmp_path):
    db = tmp_path / "runtime.db"
    fresh = (_NOW - timedelta(minutes=2)).isoformat()
    _make_runs_db(db, [("run_done", "completed", None, None, fresh)])
    _point_at(monkeypatch, db)
    assert th._todo_execution_run_live({"workflow_run_id": "run_done"}, {}, _NOW_ISO) is False


def test_no_matching_run_is_not_live(monkeypatch, tmp_path):
    db = tmp_path / "runtime.db"
    _make_runs_db(db, [("other", "running", None, None, _NOW_ISO)])
    _point_at(monkeypatch, db)
    assert th._todo_execution_run_live({"workflow_run_id": "run_missing"}, {}, _NOW_ISO) is False


def test_no_run_id_is_not_live(monkeypatch, tmp_path):
    db = tmp_path / "runtime.db"
    _make_runs_db(db, [("x", "running", None, None, _NOW_ISO)])
    _point_at(monkeypatch, db)
    # No assignment row and no metadata.dispatch.last_workflow_run_id → False.
    assert th._todo_execution_run_live(None, {}, _NOW_ISO) is False
    assert th._todo_execution_run_live({"workflow_run_id": None}, {}, _NOW_ISO) is False


def test_runs_table_absent_is_not_live(monkeypatch, tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()  # exists, but no `runs` table
    _point_at(monkeypatch, db)
    assert th._todo_execution_run_live({"workflow_run_id": "run_live"}, {}, _NOW_ISO) is False


def test_kill_switch_disables_guard(monkeypatch, tmp_path):
    db = tmp_path / "runtime.db"
    fresh = (_NOW - timedelta(minutes=1)).isoformat()
    _make_runs_db(db, [("run_live", "running", None, None, fresh)])
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(db))
    monkeypatch.setenv("UA_RECONCILE_TODO_RUN_GUARD_ENABLED", "0")
    # Even a live+fresh run returns False when the guard is killed.
    assert th._todo_execution_run_live({"workflow_run_id": "run_live"}, {}, _NOW_ISO) is False


def test_metadata_fallback_run_id(monkeypatch, tmp_path):
    db = tmp_path / "runtime.db"
    fresh = (_NOW - timedelta(minutes=3)).isoformat()
    _make_runs_db(db, [("run_meta", "running", None, None, fresh)])
    _point_at(monkeypatch, db)
    # No assignment workflow_run_id → falls back to metadata.dispatch.last_workflow_run_id.
    meta = {"dispatch": {"last_workflow_run_id": "run_meta"}}
    assert th._todo_execution_run_live({"workflow_run_id": ""}, meta, _NOW_ISO) is True
