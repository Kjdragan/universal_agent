"""Regression tests for the dispatch head-of-line block (2026-06-02).

Root cause: a finalized row reverted to status=open WITHOUT clearing its
``completion_token`` leaked into the eligible set. The claim SELECT fetched
only ``LIMIT=claim_limit`` (=1), so that single completion-locked head row was
skipped (``Skipping claim of completion-locked task``) and nothing ranked
behind it was fetched — the whole claimable backlog starved.

Two fixes are verified here:

* ``rebuild_dispatch_queue`` forces ``eligible=False`` (skip_reason
  ``completion_locked``) for any row carrying a non-empty ``completion_token``.
* ``claim_next_dispatch_tasks`` over-fetches a candidate window decoupled from
  the claim target, so a single un-claimable head row no longer starves rows
  behind it.
"""

from __future__ import annotations

import sqlite3

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_open(conn: sqlite3.Connection, task_id: str, *, rank_hint: int) -> None:
    """Seed an agent-ready, must-complete OPEN task (always eligible)."""
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "manual",
            "source_ref": "test",
            "title": f"Task {task_id}",
            "description": "Needs handling",
            "project_key": "immediate",
            # priority influences sort so the locked row ranks ahead
            "priority": rank_hint,
            "labels": ["agent-ready", "must-complete"],
            "status": task_hub.TASK_STATUS_OPEN,
            "must_complete": True,
            "agent_ready": True,
        },
    )


def test_completion_token_row_is_not_eligible() -> None:
    conn = _conn()
    try:
        _seed_open(conn, "task:locked", rank_hint=9)
        # Simulate a finalized task that leaked back to status=open WITHOUT
        # clearing its completion_token.
        conn.execute(
            "UPDATE task_hub_items SET completion_token=? WHERE task_id=?",
            ("auto_deadbeef1234_x", "task:locked"),
        )
        conn.commit()

        task_hub.rebuild_dispatch_queue(conn)
        queue = task_hub.get_dispatch_queue(conn, limit=100)
        by_id = {item["task_id"]: item for item in queue["items"]}

        assert "task:locked" in by_id
        assert by_id["task:locked"]["eligible"] is False
        assert by_id["task:locked"]["skip_reason"] == "completion_locked"
    finally:
        conn.close()


def test_claimable_row_behind_locked_head_still_claimed() -> None:
    conn = _conn()
    try:
        # Higher priority => sorts ahead in the queue (the "head" row).
        _seed_open(conn, "task:locked-head", rank_hint=9)
        _seed_open(conn, "task:claimable", rank_hint=1)
        conn.execute(
            "UPDATE task_hub_items SET completion_token=? WHERE task_id=?",
            ("auto_deadbeef1234_x", "task:locked-head"),
        )
        conn.commit()

        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="test")

        claimed_ids = {c["task_id"] for c in claimed}
        # The locked head must NOT starve the claimable row behind it.
        assert "task:claimable" in claimed_ids
        assert "task:locked-head" not in claimed_ids
    finally:
        conn.close()
