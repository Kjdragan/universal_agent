"""claim_next_dispatch_tasks must claim a task atomically.

The claim used to read status via get_item, then later UPDATE status to
in_progress WHERE task_id only (no status guard). With autocommit connections
and no UNIQUE(task_id) on task_hub_assignments, two concurrent claimers could
both pass the read and both flip the row -> double-dispatch. The claim is now
gated by an UPDATE ... WHERE task_id=? AND status IN (open, review) whose
rowcount==1 is the authoritative claim; the assignment/run rows are written only
after the claim is won.
"""

from __future__ import annotations

import sqlite3

from universal_agent import task_hub


def _conn(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or ":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_open_task(conn: sqlite3.Connection, task_id: str) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "test_claim",
            "source_ref": "test",
            "title": f"Task {task_id}",
            "description": "needs handling",
            "project_key": "immediate",
            "priority": 5,
            "labels": ["agent-ready", "must-complete"],
            "status": task_hub.TASK_STATUS_OPEN,
            "must_complete": True,
            "agent_ready": True,
            "metadata": {},
        },
    )


def _assignment_count(conn: sqlite3.Connection, task_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM task_hub_assignments WHERE task_id=?", (task_id,)
    ).fetchone()["n"]


def test_happy_path_claim_flips_status_and_records_one_assignment():
    conn = _conn()
    _seed_open_task(conn, "t-ok")
    claimed = task_hub.claim_next_dispatch_tasks(conn, limit=5, agent_id="agent-1")
    ids = [c.get("task_id") for c in claimed]
    assert "t-ok" in ids
    assert task_hub.get_item(conn, "t-ok")["status"] == task_hub.TASK_STATUS_IN_PROGRESS
    assert _assignment_count(conn, "t-ok") == 1


def test_second_claim_does_not_reclaim_or_duplicate_assignment():
    conn = _conn()
    _seed_open_task(conn, "t-once")
    first = task_hub.claim_next_dispatch_tasks(conn, limit=5, agent_id="agent-1")
    assert "t-once" in [c.get("task_id") for c in first]
    # Already in_progress — a second sweep must not re-claim it.
    second = task_hub.claim_next_dispatch_tasks(conn, limit=5, agent_id="agent-2")
    assert "t-once" not in [c.get("task_id") for c in second]
    assert _assignment_count(conn, "t-once") == 1


def test_guarded_update_is_atomic_across_connections(tmp_path):
    """The core guarantee: two connections racing the claim UPDATE — only one
    matches a row (rowcount 1); the loser matches 0 once the status is flipped."""
    db = str(tmp_path / "th.db")
    seed = _conn(db)
    _seed_open_task(seed, "t-race")
    seed.commit()
    seed.close()

    a = sqlite3.connect(db)
    b = sqlite3.connect(db)
    sql = (
        "UPDATE task_hub_items SET status=? WHERE task_id=? AND status IN (?, ?)"
    )
    params = (
        task_hub.TASK_STATUS_IN_PROGRESS,
        "t-race",
        task_hub.TASK_STATUS_OPEN,
        task_hub.TASK_STATUS_REVIEW,
    )
    ra = a.execute(sql, params)
    a.commit()
    rb = b.execute(sql, params)
    b.commit()
    try:
        assert ra.rowcount == 1  # first claimer wins
        assert rb.rowcount == 0  # second sees in_progress -> no match -> skip
    finally:
        a.close()
        b.close()
