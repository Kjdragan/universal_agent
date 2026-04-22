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
# VP lifecycle helpers -- must return hydrated rows with parsed ``metadata``
# dicts (not just the raw ``metadata_json`` column), otherwise callers wipe
# metadata or render VP review prompts with "?" placeholders.
# -----------------------------------------------------------------------


def _seed_delegated_task(
    conn: sqlite3.Connection,
    *,
    task_id: str = "deleg-001",
    mission_id: str = "mission-abc",
    extra_metadata: dict | None = None,
) -> dict:
    metadata: dict = {
        "delegation": {
            "mission_id": mission_id,
            "vp_id": "vp.coder.primary",
            "delegated_at": "2026-04-22T00:00:00+00:00",
        },
        "dispatch": {"queue_build_id": "qb-123"},
        "csi": {"routing_state": "agent_actionable"},
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    item = task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "manual",
            "source_ref": "test",
            "title": "Delegated task",
            "description": "VP is working",
            "project_key": "test",
            "priority": 2,
            "labels": ["delegated"],
            "status": task_hub.TASK_STATUS_DELEGATED,
            "metadata": metadata,
        },
    )
    # upsert_item does not change status away from what the caller asked for here,
    # but make sure status and updated_at are set as expected for downstream queries.
    conn.execute(
        "UPDATE task_hub_items SET status = ?, updated_at = ? WHERE task_id = ?",
        (task_hub.TASK_STATUS_DELEGATED, "2020-01-01T00:00:00+00:00", task_id),
    )
    conn.commit()
    return item


def test_find_delegated_task_by_mission_id_returns_hydrated_metadata() -> None:
    """find_delegated_task_by_mission_id must hydrate ``metadata`` so callers
    see the parsed delegation dict rather than a raw JSON string."""
    conn = _row_conn()
    try:
        _seed_delegated_task(conn, task_id="fd-001", mission_id="mission-find")
        task = task_hub.find_delegated_task_by_mission_id(conn, mission_id="mission-find")
        assert task is not None
        assert task["task_id"] == "fd-001"
        # Hydrated data must include the parsed metadata dict...
        assert isinstance(task.get("metadata"), dict)
        assert task["metadata"]["delegation"]["mission_id"] == "mission-find"
        # ...plus hydrated labels.
        assert "delegated" in task.get("labels", [])
    finally:
        conn.close()


def test_get_pending_review_tasks_returns_hydrated_metadata() -> None:
    """get_pending_review_tasks must surface ``metadata`` (parsed dict), not just
    ``metadata_json``, so the VP Completion Review prompt can read
    ``delegation.vp_id`` / ``mission_id`` / ``result_summary``."""
    conn = _row_conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "pr-001",
                "source_kind": "manual",
                "source_ref": "test",
                "title": "Awaiting Simone review",
                "description": "",
                "project_key": "test",
                "priority": 2,
                "status": task_hub.TASK_STATUS_PENDING_REVIEW,
                "metadata": {
                    "delegation": {
                        "mission_id": "mission-xyz",
                        "vp_id": "vp.coder.primary",
                        "vp_terminal_status": "completed",
                        "result_summary": "Shipped the fix.",
                    }
                },
            },
        )

        items = task_hub.get_pending_review_tasks(conn)
        assert len(items) == 1
        task = items[0]
        assert task["task_id"] == "pr-001"
        assert isinstance(task.get("metadata"), dict)
        delegation = task["metadata"].get("delegation") or {}
        assert delegation.get("mission_id") == "mission-xyz"
        assert delegation.get("vp_id") == "vp.coder.primary"
        assert delegation.get("vp_terminal_status") == "completed"
        assert delegation.get("result_summary") == "Shipped the fix."
    finally:
        conn.close()


def test_reopen_stale_delegations_preserves_existing_metadata() -> None:
    """reopen_stale_delegations must NOT wipe existing metadata (dispatch info,
    delegation history, CSI routing state). It should hydrate, merge new
    stale-reopen fields into ``metadata.delegation``, and persist the full dict."""
    conn = _row_conn()
    try:
        _seed_delegated_task(
            conn,
            task_id="stale-001",
            mission_id="mission-stale",
        )

        reopened = task_hub.reopen_stale_delegations(conn, stale_hours=1.0)
        assert len(reopened) == 1
        assert reopened[0]["task_id"] == "stale-001"

        # Re-read from the DB and verify the full metadata blob survived the UPDATE.
        refreshed = task_hub.get_item(conn, "stale-001")
        assert refreshed is not None
        metadata = refreshed.get("metadata") or {}
        assert isinstance(metadata, dict)

        # Original mission_id + delegated_at preserved.
        delegation = metadata.get("delegation") or {}
        assert delegation.get("mission_id") == "mission-stale"
        assert delegation.get("delegated_at") == "2026-04-22T00:00:00+00:00"
        # New stale-reopen fields added.
        assert "stale_reopened_at" in delegation
        assert delegation.get("stale_reason") == "no_vp_progress_after_1.0h"

        # Sibling metadata sections (dispatch, csi) must still be present.
        assert metadata.get("dispatch", {}).get("queue_build_id") == "qb-123"
        assert metadata.get("csi", {}).get("routing_state") == "agent_actionable"

        # Status should have been flipped back to open.
        assert refreshed["status"] == task_hub.TASK_STATUS_OPEN
    finally:
        conn.close()


def test_transition_to_pending_review_preserves_metadata() -> None:
    """transition_to_pending_review calls find_delegated_task_by_mission_id and
    merges VP completion fields into delegation metadata. Pre-existing metadata
    sections (dispatch, csi, original delegation fields) must survive."""
    conn = _row_conn()
    try:
        _seed_delegated_task(
            conn,
            task_id="tpr-001",
            mission_id="mission-complete",
        )

        updated = task_hub.transition_to_pending_review(
            conn,
            mission_id="mission-complete",
            vp_id="vp.coder.primary",
            terminal_status="completed",
            result_summary="Finished the job.",
        )
        assert updated is not None
        assert updated["task_id"] == "tpr-001"
        assert updated["status"] == task_hub.TASK_STATUS_PENDING_REVIEW

        refreshed = task_hub.get_item(conn, "tpr-001")
        assert refreshed is not None
        metadata = refreshed.get("metadata") or {}
        assert isinstance(metadata, dict)

        # New completion fields merged in.
        delegation = metadata.get("delegation") or {}
        assert delegation.get("vp_terminal_status") == "completed"
        assert delegation.get("vp_id") == "vp.coder.primary"
        assert delegation.get("result_summary") == "Finished the job."
        # Pre-existing delegation fields preserved.
        assert delegation.get("mission_id") == "mission-complete"
        assert delegation.get("delegated_at") == "2026-04-22T00:00:00+00:00"

        # Sibling metadata sections preserved.
        assert metadata.get("dispatch", {}).get("queue_build_id") == "qb-123"
        assert metadata.get("csi", {}).get("routing_state") == "agent_actionable"
    finally:
        conn.close()


