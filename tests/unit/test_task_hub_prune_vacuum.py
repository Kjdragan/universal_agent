"""Unit tests for Task Hub settled-task pruning + guarded activity-db VACUUM.

Covers the 2026-07-03 disk-reclamation extension to ``task_hub``:

* ``prune_settled_tasks`` now also prunes ``cancelled`` rows on their own
  (longer) retention, and folds in deletion of orphaned ``task_hub_runs``.
* ``vacuum_activity_db`` runs a guarded ``VACUUM`` so freed pages are returned
  to the OS (SQLite ``DELETE`` never shrinks the file on its own).

Conventions match ``test_task_hub_runs.py``: in-memory sqlite3 + real file DB
for the VACUUM path.
"""

from __future__ import annotations

import sqlite3

from universal_agent import task_hub
from universal_agent.durable.db import connect_runtime_db

# ── helpers ───────────────────────────────────────────────────────────────


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    status: str,
    days_ago: int = 0,
) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "internal",
            "title": f"task {task_id}",
            "description": "do the thing",
            "status": status,
            "agent_ready": True,
        },
    )
    if days_ago:
        # Age the row so retention-based pruning can see it as eligible.
        conn.execute(
            "UPDATE task_hub_items SET updated_at = datetime('now', ?) WHERE task_id = ?",
            (f"-{days_ago} days", task_id),
        )
        conn.commit()


def _insert_run(conn: sqlite3.Connection, run_id: str, task_id: str) -> None:
    conn.execute(
        "INSERT INTO task_hub_runs (run_id, task_id, started_at) VALUES (?, ?, ?)",
        (run_id, task_id, task_hub._now_iso()),
    )
    conn.commit()


def _count(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


# ── pruner: cancelled retention ───────────────────────────────────────────


def test_prune_removes_cancelled_past_retention() -> None:
    conn = _conn()
    _seed_task(conn, "old-cancelled", status=task_hub.TASK_STATUS_CANCELLED, days_ago=45)
    _seed_task(conn, "recent-cancelled", status=task_hub.TASK_STATUS_CANCELLED, days_ago=5)

    res = task_hub.prune_settled_tasks(conn, retention_days=21, cancelled_retention_days=30)

    assert res["items"] == 1
    assert _count(conn, "SELECT COUNT(*) FROM task_hub_items WHERE task_id='old-cancelled'") == 0
    assert _count(conn, "SELECT COUNT(*) FROM task_hub_items WHERE task_id='recent-cancelled'") == 1


def test_prune_keeps_recent_cancelled_default_window() -> None:
    """Default cancelled retention (30d) must keep a 10-day-old cancel."""
    conn = _conn()
    _seed_task(conn, "ten-day-cancelled", status=task_hub.TASK_STATUS_CANCELLED, days_ago=10)

    res = task_hub.prune_settled_tasks(conn, retention_days=21)

    assert res["items"] == 0
    assert _count(conn, "SELECT COUNT(*) FROM task_hub_items WHERE task_id='ten-day-cancelled'") == 1


def test_prune_cancelled_skipped_when_zero() -> None:
    """cancelled_retention_days=0 opts out of cancelled pruning entirely."""
    conn = _conn()
    _seed_task(conn, "ancient-cancelled", status=task_hub.TASK_STATUS_CANCELLED, days_ago=400)

    res = task_hub.prune_settled_tasks(conn, retention_days=21, cancelled_retention_days=0)

    assert res["items"] == 0
    assert _count(conn, "SELECT COUNT(*) FROM task_hub_items WHERE task_id='ancient-cancelled'") == 1


def test_prune_still_removes_completed_and_parked() -> None:
    """The pre-existing completed/parked behavior is unchanged."""
    conn = _conn()
    _seed_task(conn, "old-completed", status=task_hub.TASK_STATUS_COMPLETED, days_ago=40)
    _seed_task(conn, "old-parked", status=task_hub.TASK_STATUS_PARKED, days_ago=40)
    _seed_task(conn, "fresh-completed", status=task_hub.TASK_STATUS_COMPLETED, days_ago=2)

    res = task_hub.prune_settled_tasks(conn, retention_days=21, cancelled_retention_days=30)

    assert res["items"] == 2
    assert _count(conn, "SELECT COUNT(*) FROM task_hub_items WHERE task_id='old-completed'") == 0
    assert _count(conn, "SELECT COUNT(*) FROM task_hub_items WHERE task_id='old-parked'") == 0
    assert _count(conn, "SELECT COUNT(*) FROM task_hub_items WHERE task_id='fresh-completed'") == 1


# ── pruner: orphaned task_hub_runs ────────────────────────────────────────


def test_prune_removes_orphaned_runs() -> None:
    """Runs whose parent task no longer exists are swept."""
    conn = _conn()
    _seed_task(conn, "live-open", status=task_hub.TASK_STATUS_OPEN)  # not pruned
    _insert_run(conn, "run-live", "live-open")        # has a parent → kept
    _insert_run(conn, "run-ghost", "no-such-task")    # orphan → removed

    res = task_hub.prune_settled_tasks(conn, retention_days=21, cancelled_retention_days=30)

    assert res["runs"] == 1
    assert _count(conn, "SELECT COUNT(*) FROM task_hub_runs WHERE run_id='run-live'") == 1
    assert _count(conn, "SELECT COUNT(*) FROM task_hub_runs WHERE run_id='run-ghost'") == 0


def test_prune_runs_follow_pruned_tasks() -> None:
    """A run attached to a task pruned in this pass becomes an orphan and is removed too."""
    conn = _conn()
    _seed_task(conn, "old-completed", status=task_hub.TASK_STATUS_COMPLETED, days_ago=40)
    _insert_run(conn, "run-old", "old-completed")

    res = task_hub.prune_settled_tasks(conn, retention_days=21, cancelled_retention_days=30)

    assert res["items"] == 1
    assert res["runs"] == 1
    assert _count(conn, "SELECT COUNT(*) FROM task_hub_runs WHERE run_id='run-old'") == 0


# ── vacuum: gate, throttle, window ────────────────────────────────────────


def _seed_file_db(db_path: str) -> None:
    conn = connect_runtime_db(db_path)
    try:
        task_hub.ensure_schema(conn)
        conn.execute(
            "INSERT INTO task_hub_items (task_id, source_kind, title, description, "
            "status, agent_ready, created_at, updated_at) "
            "VALUES ('seed', 'internal', 'seed', 'seed', 'completed', 0, ?, ?)",
            (task_hub._now_iso(), task_hub._now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def test_vacuum_not_invoked_when_gate_off(tmp_path) -> None:
    db_path = str(tmp_path / "act.db")
    _seed_file_db(db_path)

    import universal_agent.durable.db as dbmod

    calls: dict[str, int] = {"n": 0}
    real_connect = dbmod.connect_runtime_db

    def spy(path=None):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return real_connect(path)

    # vacuum_activity_db imports connect_runtime_db from this module lazily at
    # call time, so the monkeypatch is seen.
    dbmod.connect_runtime_db = spy  # type: ignore[assignment]
    try:
        res = task_hub.vacuum_activity_db(db_path=db_path, enabled=False)
    finally:
        dbmod.connect_runtime_db = real_connect  # type: ignore[assignment]

    assert res["vacuumed"] is False
    assert res["reason"] == "disabled"
    # Gate off → the function must return before opening any DB connection.
    assert calls["n"] == 0


def test_vacuum_runs_when_enabled_and_stamps(tmp_path) -> None:
    db_path = str(tmp_path / "act.db")
    _seed_file_db(db_path)

    res = task_hub.vacuum_activity_db(
        db_path=db_path,
        enabled=True,
        within_window=lambda: True,
        min_interval_hours=24,
    )

    assert res["vacuumed"] is True
    assert res["reason"] if "reason" in res else True
    assert "before_bytes" in res and "after_bytes" in res and "reclaimed_bytes" in res

    # The throttle stamp must be persisted in task_hub_settings.
    conn = connect_runtime_db(db_path)
    try:
        stamp = task_hub._get_setting(conn, task_hub._VACUUM_SETTING_KEY)
    finally:
        conn.close()
    assert stamp.get("last_vacuum_at")


def test_vacuum_throttled_after_recent_run(tmp_path) -> None:
    db_path = str(tmp_path / "act.db")
    _seed_file_db(db_path)

    first = task_hub.vacuum_activity_db(
        db_path=db_path, enabled=True, within_window=lambda: True, min_interval_hours=24
    )
    assert first["vacuumed"] is True

    second = task_hub.vacuum_activity_db(
        db_path=db_path, enabled=True, within_window=lambda: True, min_interval_hours=24
    )
    assert second["vacuumed"] is False
    assert second["reason"] == "throttled"


def test_vacuum_respects_off_hours_window(tmp_path) -> None:
    db_path = str(tmp_path / "act.db")
    _seed_file_db(db_path)

    res = task_hub.vacuum_activity_db(
        db_path=db_path, enabled=True, within_window=lambda: False, min_interval_hours=24
    )
    assert res["vacuumed"] is False
    assert res["reason"] == "outside_window"
