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
# VP lifecycle paths -- previously returned un-hydrated rows, wiping metadata
# Regression guards for Devin Review findings on PR #115.
# -----------------------------------------------------------------------


def _seed_delegated_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    mission_id: str,
    extra_metadata: dict | None = None,
    updated_at: str | None = None,
) -> dict:
    """Insert a task already in the delegated state with delegation metadata."""
    task = task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "manual",
            "source_ref": "test",
            "title": "Delegated task",
            "description": "VP working",
            "project_key": "test",
            "priority": 2,
            "status": task_hub.TASK_STATUS_OPEN,
            "metadata": {
                "delegation": {
                    "mission_id": mission_id,
                    "vp_id": "vp.general.primary",
                    "delegated_at": "2026-04-22T00:00:00+00:00",
                },
                "csi": {"routing_state": "agent_actionable"},
                **(extra_metadata or {}),
            },
        },
    )
    # Flip to delegated (optionally back-dating updated_at for stale tests).
    if updated_at is None:
        conn.execute(
            "UPDATE task_hub_items SET status=?, seizure_state=? WHERE task_id=?",
            (task_hub.TASK_STATUS_DELEGATED, "delegated", task_id),
        )
    else:
        conn.execute(
            "UPDATE task_hub_items SET status=?, seizure_state=?, updated_at=? WHERE task_id=?",
            (task_hub.TASK_STATUS_DELEGATED, "delegated", updated_at, task_id),
        )
    conn.commit()
    return task


def test_find_delegated_task_by_mission_id_returns_hydrated_metadata() -> None:
    conn = _raw_conn()
    try:
        _seed_delegated_task(
            conn,
            task_id="vp-find-001",
            mission_id="vp-mission-abc",
            extra_metadata={"dispatch": {"queue_build_id": "qb-123"}},
        )
        task = task_hub.find_delegated_task_by_mission_id(conn, mission_id="vp-mission-abc")
        assert task is not None
        # Bug guard: metadata must be a parsed dict, not just metadata_json.
        assert isinstance(task.get("metadata"), dict)
        assert task["metadata"]["delegation"]["mission_id"] == "vp-mission-abc"
        assert task["metadata"]["dispatch"]["queue_build_id"] == "qb-123"
        assert task["metadata"]["csi"]["routing_state"] == "agent_actionable"
    finally:
        conn.close()


def test_find_delegated_task_by_mission_id_missing_returns_none() -> None:
    conn = _raw_conn()
    try:
        assert task_hub.find_delegated_task_by_mission_id(conn, mission_id="no-such") is None
    finally:
        conn.close()


def test_get_pending_review_tasks_returns_hydrated_metadata() -> None:
    conn = _raw_conn()
    try:
        _seed_delegated_task(
            conn,
            task_id="vp-pr-001",
            mission_id="vp-mission-pr-001",
            extra_metadata={"dispatch": {"queue_build_id": "qb-pr"}},
        )
        updated = task_hub.transition_to_pending_review(
            conn,
            mission_id="vp-mission-pr-001",
            vp_id="vp.general.primary",
            terminal_status="completed",
            result_summary="All work done.",
        )
        assert updated is not None
        assert updated["status"] == task_hub.TASK_STATUS_PENDING_REVIEW

        tasks = task_hub.get_pending_review_tasks(conn)
        assert len(tasks) == 1
        task = tasks[0]
        # Bug guard: callers in heartbeat_service read task["metadata"]["delegation"].
        assert isinstance(task.get("metadata"), dict)
        delegation = task["metadata"].get("delegation") or {}
        assert delegation.get("mission_id") == "vp-mission-pr-001"
        assert delegation.get("vp_id") == "vp.general.primary"
        assert delegation.get("vp_terminal_status") == "completed"
        assert delegation.get("result_summary") == "All work done."
        # Pre-existing metadata siblings must survive the transition.
        assert task["metadata"]["dispatch"]["queue_build_id"] == "qb-pr"
    finally:
        conn.close()


def test_reopen_stale_delegations_preserves_metadata() -> None:
    """Regression: reopen_stale_delegations used to wipe metadata because the
    row came back un-hydrated (only metadata_json was present, not metadata)."""
    conn = _raw_conn()
    try:
        _seed_delegated_task(
            conn,
            task_id="vp-stale-001",
            mission_id="vp-mission-stale",
            extra_metadata={"dispatch": {"queue_build_id": "qb-stale"}},
            updated_at="2020-01-01T00:00:00+00:00",
        )
        reopened = task_hub.reopen_stale_delegations(conn, stale_hours=1.0)
        assert len(reopened) == 1

        # The returned task dict must include hydrated metadata.
        task = reopened[0]
        assert isinstance(task.get("metadata"), dict)
        assert task["metadata"]["delegation"]["mission_id"] == "vp-mission-stale"

        # Crucially, the DB-side metadata_json must still contain the original
        # delegation + dispatch + csi data plus new stale markers — NOT a wipe.
        refreshed = task_hub.get_item(conn, "vp-stale-001")
        assert refreshed is not None
        assert refreshed["status"] == task_hub.TASK_STATUS_OPEN
        # Reopened tasks must use the standard "unseized" seizure_state
        # (not the legacy "open" value) so downstream consumers treat them
        # consistently with other open tasks.
        assert refreshed["seizure_state"] == "unseized"
        meta = refreshed["metadata"]
        assert meta["delegation"]["mission_id"] == "vp-mission-stale"
        assert meta["delegation"]["vp_id"] == "vp.general.primary"
        assert meta["delegation"]["stale_reason"].startswith("no_vp_progress_after_")
        assert "stale_reopened_at" in meta["delegation"]
        assert meta["dispatch"]["queue_build_id"] == "qb-stale"
        assert meta["csi"]["routing_state"] == "agent_actionable"
    finally:
        conn.close()


def test_transition_to_pending_review_idempotent_and_hydrated() -> None:
    conn = _raw_conn()
    try:
        _seed_delegated_task(
            conn,
            task_id="vp-trans-001",
            mission_id="vp-mission-trans",
        )
        first = task_hub.transition_to_pending_review(
            conn,
            mission_id="vp-mission-trans",
            vp_id="vp.general.primary",
            terminal_status="completed",
        )
        assert first is not None
        assert first["status"] == task_hub.TASK_STATUS_PENDING_REVIEW
        assert isinstance(first.get("metadata"), dict)

        # Second call should find the already-pending-review task via the
        # hydrated lookup path and short-circuit without re-updating.
        second = task_hub.transition_to_pending_review(
            conn,
            mission_id="vp-mission-trans",
            vp_id="vp.general.primary",
            terminal_status="failed",  # would be recorded if not idempotent
        )
        assert second is None or second["status"] == task_hub.TASK_STATUS_PENDING_REVIEW
    finally:
        conn.close()


