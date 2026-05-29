"""Regression tests: clearing a completed task off the dashboard must NOT
demote it to ``parked``.

Background: the "clear completed" dashboard action used to flip
``status: completed -> parked`` (stamping ``stale_state=dashboard_hidden``).
Because ``parked`` is a terminal-but-deferred status, Simone's morning digest
read these archived-done rows as a "parked backlog needing triage" — a
recurring false positive (e.g. the May 29 2026 digest reported 6 parked
proactive_codie tasks that had in fact completed, one shipping merged PR #527).

The fix keeps such rows ``status=completed`` and relies on
``stale_state=dashboard_hidden`` to drop them off the completed tab. This file
pins that contract plus the new terminal-disposition stamp that lets readers
tell a shipped-PR completion apart from a legitimate no-op.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from universal_agent import task_hub
from universal_agent.vp.worker_loop import VpWorkerLoop


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_completed(conn: sqlite3.Connection, *, task_id: str) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "proactive_codie",
            "title": f"completed {task_id}",
            "description": "",
            "status": task_hub.TASK_STATUS_COMPLETED,
            "agent_ready": False,
            "parent_task_id": "owner",  # satisfy mirror-has-parent visibility clause
        },
    )


def _hide(conn: sqlite3.Connection, task_id: str) -> None:
    """Mirror the dashboard 'clear completed' UPDATE (status untouched)."""
    conn.execute(
        "UPDATE task_hub_items SET stale_state=? WHERE task_id=?",
        (task_hub.STALE_STATE_DASHBOARD_HIDDEN, task_id),
    )
    conn.commit()


def _completed_ids(conn: sqlite3.Connection) -> set[str]:
    return {str(r["task_id"]) for r in task_hub.list_completed_tasks(conn, limit=100)}


def test_hidden_completed_stays_completed_and_drops_off_tab() -> None:
    conn = _conn()
    _seed_completed(conn, task_id="t-visible")
    _seed_completed(conn, task_id="t-hidden")
    _hide(conn, "t-hidden")

    # Status is NOT demoted to parked — it remains a terminal SUCCESS.
    row = conn.execute(
        "SELECT status, stale_state FROM task_hub_items WHERE task_id='t-hidden'"
    ).fetchone()
    assert row["status"] == task_hub.TASK_STATUS_COMPLETED
    assert row["stale_state"] == task_hub.STALE_STATE_DASHBOARD_HIDDEN

    # But it no longer appears on the completed tab; the un-hidden one still does.
    ids = _completed_ids(conn)
    assert "t-visible" in ids
    assert "t-hidden" not in ids


def test_completed_is_not_in_parked_status_count() -> None:
    """A hidden-completed row must not inflate the ``parked`` status count
    that the digest groups on."""
    conn = _conn()
    _seed_completed(conn, task_id="t-hidden")
    _hide(conn, "t-hidden")
    parked = conn.execute(
        "SELECT COUNT(*) c FROM task_hub_items WHERE status=?",
        (task_hub.TASK_STATUS_PARKED,),
    ).fetchone()["c"]
    assert parked == 0


class _Outcome:
    def __init__(self, message: str = "", result_ref: str = "", payload: dict[str, Any] | None = None):
        self.message = message
        self.result_ref = result_ref
        self.payload = payload or {}


def test_detect_pr_url_finds_github_pr() -> None:
    out = _Outcome(message="Opened https://github.com/Kjdragan/universal_agent/pull/527 for review")
    assert VpWorkerLoop._detect_pr_url(out) == "https://github.com/Kjdragan/universal_agent/pull/527"


def test_detect_pr_url_noop_returns_empty() -> None:
    out = _Outcome(message="Inspected the module; no worthwhile cleanup found. No PR opened.")
    assert VpWorkerLoop._detect_pr_url(out) == ""
