"""Tests verifying task_hub functions work correctly regardless of row_factory.

These tests exercise the bug fix for dict(row) hydration crashes that occurred
when a raw sqlite3.Connection (without row_factory=sqlite3.Row) was passed to
task_hub functions.
"""
from __future__ import annotations

import sqlite3

from universal_agent import task_hub


def _raw_conn() -> sqlite3.Connection:
    """Return a bare in-memory connection WITHOUT row_factory set."""
    conn = sqlite3.connect(":memory:")
    # Explicitly NOT setting conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _row_conn() -> sqlite3.Connection:
    """Return a connection WITH row_factory set (the historical pattern)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_task(conn: sqlite3.Connection, task_id: str = "test-001") -> dict:
    return task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "manual",
            "source_ref": "test",
            "title": "Test task",
            "description": "Test description",
            "project_key": "test",
            "priority": 2,
            "labels": ["test"],
            "status": task_hub.TASK_STATUS_OPEN,
        },
    )


# -----------------------------------------------------------------------
# get_item -- the primary crash site from the original bug report
# -----------------------------------------------------------------------


def test_get_item_raw_connection_returns_dict() -> None:
    """get_item must return a proper dict even without row_factory."""
    conn = _raw_conn()
    try:
        _seed_task(conn, "raw-001")
        result = task_hub.get_item(conn, "raw-001")
        assert result is not None
        assert isinstance(result, dict)
        assert result["task_id"] == "raw-001"
        assert result["title"] == "Test task"
    finally:
        conn.close()


def test_get_item_row_factory_connection_still_works() -> None:
    """get_item must still work when row_factory IS set (regression guard)."""
    conn = _row_conn()
    try:
        _seed_task(conn, "row-001")
        result = task_hub.get_item(conn, "row-001")
        assert result is not None
        assert isinstance(result, dict)
        assert result["task_id"] == "row-001"
    finally:
        conn.close()


def test_get_item_nonexistent_returns_none() -> None:
    conn = _raw_conn()
    try:
        assert task_hub.get_item(conn, "no-such-id") is None
    finally:
        conn.close()


# -----------------------------------------------------------------------
# list functions that iterate rows with dict(row)
# -----------------------------------------------------------------------


def test_rebuild_dispatch_queue_raw_connection() -> None:
    conn = _raw_conn()
    try:
        _seed_task(conn, "dispatch-001")
        result = task_hub.rebuild_dispatch_queue(conn)
        assert isinstance(result, dict)
        assert "queue_build_id" in result
        assert result["items_total"] >= 1
    finally:
        conn.close()


def test_list_due_scheduled_tasks_raw_connection() -> None:
    conn = _raw_conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "sched-001",
                "source_kind": "system_command",
                "source_ref": "test",
                "title": "Scheduled task",
                "description": "Due already",
                "project_key": "test",
                "priority": 1,
                "status": task_hub.TASK_STATUS_OPEN,
                "trigger_type": "scheduled",
                "due_at": "2020-01-01T00:00:00+00:00",
            },
        )
        items = task_hub.list_due_scheduled_tasks(conn, as_of_iso="2099-01-01T00:00:00+00:00")
        assert isinstance(items, list)
        assert len(items) == 1
        assert items[0]["task_id"] == "sched-001"
    finally:
        conn.close()


def test_list_completed_tasks_raw_connection() -> None:
    conn = _raw_conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "done-001",
                "source_kind": "manual",
                "source_ref": "test",
                "title": "Done task",
                "description": "",
                "project_key": "test",
                "priority": 1,
                "status": task_hub.TASK_STATUS_COMPLETED,
            },
        )
        items = task_hub.list_completed_tasks(conn)
        assert isinstance(items, list)
        assert any(i["task_id"] == "done-001" for i in items)
    finally:
        conn.close()


def test_list_personal_queue_raw_connection() -> None:
    conn = _raw_conn()
    try:
        _seed_task(conn, "pq-001")
        items = task_hub.list_personal_queue(conn)
        assert isinstance(items, list)
    finally:
        conn.close()


def test_list_subtasks_raw_connection() -> None:
    conn = _raw_conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "parent-001",
                "source_kind": "manual",
                "source_ref": "test",
                "title": "Parent task",
                "description": "",
                "project_key": "test",
                "priority": 1,
                "status": task_hub.TASK_STATUS_OPEN,
            },
        )
        task_hub.upsert_item(
            conn,
            {
                "task_id": "child-001",
                "source_kind": "manual",
                "source_ref": "test",
                "title": "Child task",
                "description": "",
                "project_key": "test",
                "priority": 1,
                "status": task_hub.TASK_STATUS_OPEN,
                "parent_task_id": "parent-001",
            },
        )
        children = task_hub.list_subtasks(conn, "parent-001")
        assert isinstance(children, list)
        assert len(children) == 1
        assert children[0]["task_id"] == "child-001"
    finally:
        conn.close()


# -----------------------------------------------------------------------
# ensure_schema sets row_factory -- the actual fix mechanism
# -----------------------------------------------------------------------


def test_ensure_schema_sets_row_factory() -> None:
    """ensure_schema should set row_factory so subsequent fetches work."""
    conn = sqlite3.connect(":memory:")
    assert conn.row_factory is None  # default
    task_hub.ensure_schema(conn)
    assert conn.row_factory is sqlite3.Row
    conn.close()


# -----------------------------------------------------------------------
# Question queue functions that use dict(row)
# -----------------------------------------------------------------------


def test_question_queue_raw_connection() -> None:
    conn = _raw_conn()
    try:
        _seed_task(conn, "q-001")
        q = task_hub.enqueue_question(
            conn, task_id="q-001", question_text="Is it done?", channel="dashboard"
        )
        assert q["question_id"]

        pending = task_hub.list_pending_questions(conn)
        assert len(pending) >= 1

        answered = task_hub.answer_question(
            conn, question_id=q["question_id"], answer_text="Yes"
        )
        assert answered["answered"] == 1 or answered.get("answered") is True

        expiring = task_hub.list_expiring_questions(conn)
        assert isinstance(expiring, list)
    finally:
        conn.close()


# -----------------------------------------------------------------------
# Comments -- uses dict(row) in list_comments
# -----------------------------------------------------------------------


def test_list_comments_raw_connection() -> None:
    conn = _raw_conn()
    try:
        _seed_task(conn, "comment-001")
        task_hub.add_comment(
            conn,
            task_id="comment-001",
            author="tester",
            content="A comment",
        )
        comments = task_hub.list_comments(conn, "comment-001")
        assert len(comments) >= 1
        assert comments[0]["content"] == "A comment"
    finally:
        conn.close()


# -----------------------------------------------------------------------
# VP lifecycle functions -- must return hydrated rows with parsed `metadata`,
# not raw DB columns with `metadata_json`. A regression here silently wipes
# delegation metadata on stale-reopen and makes the VP review prompt useless.
# -----------------------------------------------------------------------


def _seed_delegated_task(
    conn: sqlite3.Connection,
    task_id: str,
    mission_id: str,
    *,
    status: str | None = None,
    delegated_at: str = "2020-01-01T00:00:00+00:00",
) -> dict:
    """Insert a task already in the delegated state with full delegation metadata."""
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "manual",
            "source_ref": "test",
            "title": f"Delegated task {task_id}",
            "description": "VP is working this",
            "project_key": "test",
            "priority": 2,
            "labels": ["test"],
            "status": task_hub.TASK_STATUS_OPEN,
            "metadata": {
                "delegation": {
                    "mission_id": mission_id,
                    "vp_id": "vp.coder.primary",
                    "delegated_at": delegated_at,
                },
                "dispatch": {"reason": "seed-for-test"},
            },
        },
    )
    # Flip to delegated (and optionally override updated_at for staleness).
    target_status = status or task_hub.TASK_STATUS_DELEGATED
    conn.execute(
        "UPDATE task_hub_items SET status=?, seizure_state=?, updated_at=? WHERE task_id=?",
        (target_status, "seized", delegated_at, task_id),
    )
    conn.commit()
    result = task_hub.get_item(conn, task_id)
    assert result is not None
    return result


def test_find_delegated_task_by_mission_id_returns_hydrated_metadata() -> None:
    """find_delegated_task_by_mission_id must return parsed `metadata`, not raw `metadata_json`."""
    conn = _row_conn()
    try:
        _seed_delegated_task(conn, "deleg-001", "mission-abc-123")

        result = task_hub.find_delegated_task_by_mission_id(conn, mission_id="mission-abc-123")
        assert result is not None
        assert result["task_id"] == "deleg-001"
        # Hydrated: `metadata` is a parsed dict, not a JSON string.
        assert isinstance(result.get("metadata"), dict)
        assert result["metadata"].get("delegation", {}).get("mission_id") == "mission-abc-123"
        assert result["metadata"].get("delegation", {}).get("vp_id") == "vp.coder.primary"
        # Labels are also hydrated to a list.
        assert isinstance(result.get("labels"), list)
    finally:
        conn.close()


def test_find_delegated_task_by_mission_id_raw_connection() -> None:
    """Same as above but on a connection without pre-set row_factory."""
    conn = _raw_conn()
    try:
        _seed_delegated_task(conn, "deleg-raw-001", "mission-raw-1")

        result = task_hub.find_delegated_task_by_mission_id(conn, mission_id="mission-raw-1")
        assert result is not None
        assert isinstance(result.get("metadata"), dict)
        assert result["metadata"]["delegation"]["mission_id"] == "mission-raw-1"
    finally:
        conn.close()


def test_get_pending_review_tasks_returns_hydrated_metadata() -> None:
    """get_pending_review_tasks must expose parsed `metadata` so the review prompt can
    read delegation.vp_id / mission_id / vp_terminal_status / result_summary."""
    conn = _row_conn()
    try:
        _seed_delegated_task(conn, "rev-001", "mission-rev-1")
        updated = task_hub.transition_to_pending_review(
            conn,
            mission_id="mission-rev-1",
            vp_id="vp.coder.primary",
            terminal_status="completed",
            result_summary="Refactored module and added tests.",
        )
        assert updated is not None
        assert updated["status"] == task_hub.TASK_STATUS_PENDING_REVIEW

        tasks = task_hub.get_pending_review_tasks(conn)
        assert len(tasks) == 1
        task = tasks[0]
        assert task["task_id"] == "rev-001"
        # Critical: caller accesses `metadata` (not `metadata_json`) in the prompt.
        assert isinstance(task.get("metadata"), dict)
        delegation = task["metadata"].get("delegation") or {}
        assert delegation.get("mission_id") == "mission-rev-1"
        assert delegation.get("vp_id") == "vp.coder.primary"
        assert delegation.get("vp_terminal_status") == "completed"
        assert delegation.get("result_summary") == "Refactored module and added tests."
    finally:
        conn.close()


def test_transition_to_pending_review_preserves_existing_metadata() -> None:
    """transition_to_pending_review must not wipe unrelated metadata keys."""
    conn = _row_conn()
    try:
        _seed_delegated_task(conn, "trans-001", "mission-trans-1")

        task_hub.transition_to_pending_review(
            conn,
            mission_id="mission-trans-1",
            vp_id="vp.general.primary",
            terminal_status="completed",
            result_summary="Done.",
        )

        refreshed = task_hub.get_item(conn, "trans-001")
        assert refreshed is not None
        metadata = refreshed.get("metadata") or {}
        # Pre-existing keys survive.
        assert metadata.get("dispatch", {}).get("reason") == "seed-for-test"
        # Delegation got enriched, not replaced.
        delegation = metadata.get("delegation") or {}
        assert delegation.get("mission_id") == "mission-trans-1"
        assert delegation.get("vp_id") == "vp.general.primary"
        assert delegation.get("vp_terminal_status") == "completed"
        assert delegation.get("result_summary") == "Done."
    finally:
        conn.close()


def test_reopen_stale_delegations_preserves_metadata() -> None:
    """reopen_stale_delegations must not wipe delegation metadata when reopening.

    Regression guard: before the hydration fix, `_row_to_dict(row)` returned raw
    columns with `metadata_json` (a JSON string) but no `metadata` key. The reopen
    path then did `dict(task.get("metadata") or {})` → `{}` and persisted an empty
    metadata dict, destroying the mission_id, dispatch history, etc.
    """
    conn = _row_conn()
    try:
        _seed_delegated_task(
            conn,
            "stale-001",
            "mission-stale-1",
            delegated_at="2020-01-01T00:00:00+00:00",
        )

        reopened = task_hub.reopen_stale_delegations(conn, stale_hours=1.0)
        assert len(reopened) == 1
        assert reopened[0]["task_id"] == "stale-001"
        # Pre-update snapshot is hydrated.
        assert isinstance(reopened[0].get("metadata"), dict)

        refreshed = task_hub.get_item(conn, "stale-001")
        assert refreshed is not None
        assert refreshed["status"] == task_hub.TASK_STATUS_OPEN
        # Reopened tasks must return to the 'unseized' seizure_state so they
        # can be re-dispatched; 'open' is not a recognised seizure_state value.
        assert refreshed["seizure_state"] == "unseized"
        metadata = refreshed.get("metadata") or {}
        delegation = metadata.get("delegation") or {}
        # The mission_id, vp_id, and original delegated_at must all survive.
        assert delegation.get("mission_id") == "mission-stale-1"
        assert delegation.get("vp_id") == "vp.coder.primary"
        assert delegation.get("delegated_at") == "2020-01-01T00:00:00+00:00"
        # Reopen bookkeeping is appended.
        assert delegation.get("stale_reopened_at")
        assert "no_vp_progress_after" in str(delegation.get("stale_reason", ""))
        # Unrelated metadata survives too.
        assert metadata.get("dispatch", {}).get("reason") == "seed-for-test"
    finally:
        conn.close()


def test_reopen_stale_delegations_raw_connection() -> None:
    """Same as above but without pre-set row_factory on the connection."""
    conn = _raw_conn()
    try:
        _seed_delegated_task(
            conn,
            "stale-raw-001",
            "mission-stale-raw-1",
            delegated_at="2020-01-01T00:00:00+00:00",
        )

        reopened = task_hub.reopen_stale_delegations(conn, stale_hours=1.0)
        assert len(reopened) == 1

        refreshed = task_hub.get_item(conn, "stale-raw-001")
        assert refreshed is not None
        delegation = (refreshed.get("metadata") or {}).get("delegation") or {}
        assert delegation.get("mission_id") == "mission-stale-raw-1"
    finally:
        conn.close()


def test_reopen_stale_delegations_skips_fresh_delegations() -> None:
    """Tasks updated within the staleness window must not be reopened."""
    conn = _row_conn()
    try:
        from datetime import datetime, timezone

        fresh_iso = datetime.now(timezone.utc).isoformat()
        _seed_delegated_task(
            conn,
            "fresh-001",
            "mission-fresh-1",
            delegated_at=fresh_iso,
        )

        reopened = task_hub.reopen_stale_delegations(conn, stale_hours=4.0)
        assert reopened == []

        refreshed = task_hub.get_item(conn, "fresh-001")
        assert refreshed is not None
        assert refreshed["status"] == task_hub.TASK_STATUS_DELEGATED
    finally:
        conn.close()


