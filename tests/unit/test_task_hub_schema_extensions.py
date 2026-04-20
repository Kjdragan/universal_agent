"""Tests for Task Hub schema extensions added during the Todoist decommission.

Covers:
- parent_task_id / subtask hierarchy (list_subtasks, get_parent_progress, decompose_task, get_decomposition_tree)
- task_hub_comments (add_comment, list_comments)
- task_hub_question_queue (enqueue_question, list_pending_questions, answer_question)
- refinement lifecycle (advance_refinement, get_refinement_state)
- trigger_type column default
"""
from __future__ import annotations

import sqlite3
import time

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _make_task(conn: sqlite3.Connection, task_id: str, **kwargs) -> dict:
    """Convenience helper to create a minimal task item."""
    item = {
        "task_id": task_id,
        "source_kind": kwargs.get("source_kind", "internal"),
        "title": kwargs.get("title", f"Test {task_id}"),
        "description": kwargs.get("description", ""),
        "project_key": kwargs.get("project_key", "immediate"),
        "priority": kwargs.get("priority", 2),
        "labels": kwargs.get("labels", ["agent-ready"]),
        "status": kwargs.get("status", task_hub.TASK_STATUS_OPEN),
        "agent_ready": kwargs.get("agent_ready", True),
    }
    item.update(kwargs)
    return task_hub.upsert_item(conn, item)


# ─── Comments ────────────────────────────────────────────────────────────────


def test_add_and_list_comments() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:comment-test")

        c1 = task_hub.add_comment(conn, task_id="task:comment-test", content="First note", author="agent")
        c2 = task_hub.add_comment(conn, task_id="task:comment-test", content="Second note", author="human")

        assert c1["comment_id"] != c2["comment_id"]
        assert c1["author"] == "agent"
        assert c2["content"] == "Second note"

        comments = task_hub.list_comments(conn, "task:comment-test")
        assert len(comments) == 2
        # Newest first
        assert comments[0]["content"] == "Second note"
        assert comments[1]["content"] == "First note"
    finally:
        conn.close()


def test_list_comments_default_author_is_system() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:default-author")
        c = task_hub.add_comment(conn, task_id="task:default-author", content="auto")
        assert c["author"] == "system"
    finally:
        conn.close()


def test_list_comments_respects_limit() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:limit-test")
        for i in range(5):
            task_hub.add_comment(conn, task_id="task:limit-test", content=f"Note {i}")

        comments = task_hub.list_comments(conn, "task:limit-test", limit=3)
        assert len(comments) == 3
    finally:
        conn.close()


def test_comments_are_scoped_to_task() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:a")
        _make_task(conn, "task:b")
        task_hub.add_comment(conn, task_id="task:a", content="For A")
        task_hub.add_comment(conn, task_id="task:b", content="For B")

        assert len(task_hub.list_comments(conn, "task:a")) == 1
        assert len(task_hub.list_comments(conn, "task:b")) == 1
    finally:
        conn.close()


# ─── Question Queue ──────────────────────────────────────────────────────────


def test_enqueue_and_list_pending_questions() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:q-test")
        q = task_hub.enqueue_question(
            conn, task_id="task:q-test", question_text="What priority?", channel="slack",
        )
        assert q["question_id"]
        assert q["question_text"] == "What priority?"
        assert q["channel"] == "slack"

        pending = task_hub.list_pending_questions(conn)
        assert len(pending) == 1
        assert pending[0]["question_text"] == "What priority?"
    finally:
        conn.close()


def test_answer_question_marks_answered() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:answer-test")
        q = task_hub.enqueue_question(
            conn, task_id="task:answer-test", question_text="Go or no go?",
        )
        qid = q["question_id"]

        answered = task_hub.answer_question(conn, question_id=qid, answer_text="Go!")
        assert answered["answered"] == 1
        assert answered["answer_text"] == "Go!"

        # Should no longer appear in pending
        pending = task_hub.list_pending_questions(conn)
        assert len(pending) == 0
    finally:
        conn.close()


def test_expired_questions_not_in_pending() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:expire-test")
        # expires_minutes=0 should immediately expire
        task_hub.enqueue_question(
            conn, task_id="task:expire-test", question_text="Too late?", expires_minutes=0,
        )
        # Add a tiny sleep so "now" in list_pending_questions is slightly after creation
        time.sleep(0.05)
        pending = task_hub.list_pending_questions(conn)
        assert len(pending) == 0
    finally:
        conn.close()


# ─── Subtasks / Decomposition ────────────────────────────────────────────────


def test_decompose_task_creates_children() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:parent")

        children = task_hub.decompose_task(
            conn,
            parent_task_id="task:parent",
            subtasks=[
                {"title": "Step 1", "description": "First step", "priority": 3},
                {"title": "Step 2", "description": "Second step", "priority": 2},
            ],
        )

        assert len(children) == 2
        assert children[0]["task_id"] == "task:parent:sub:1"
        assert children[1]["task_id"] == "task:parent:sub:2"
        assert children[0]["title"] == "Step 1"
    finally:
        conn.close()


def test_list_subtasks_returns_children_ordered() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:parent2")
        task_hub.decompose_task(
            conn,
            parent_task_id="task:parent2",
            subtasks=[
                {"title": "Low", "priority": 1},
                {"title": "High", "priority": 4},
                {"title": "Mid", "priority": 2},
            ],
        )

        subs = task_hub.list_subtasks(conn, "task:parent2")
        assert len(subs) == 3
        # Ordered by priority DESC
        assert subs[0]["title"] == "High"
        assert subs[1]["title"] == "Mid"
        assert subs[2]["title"] == "Low"
    finally:
        conn.close()


def test_get_parent_progress_tracks_status_counts() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:progress-parent")
        task_hub.decompose_task(
            conn,
            parent_task_id="task:progress-parent",
            subtasks=[
                {"title": "Done", "status": task_hub.TASK_STATUS_COMPLETED},
                {"title": "Done2", "status": task_hub.TASK_STATUS_COMPLETED},
                {"title": "Open", "status": task_hub.TASK_STATUS_OPEN},
            ],
        )

        progress = task_hub.get_parent_progress(conn, "task:progress-parent")
        assert progress["total"] == 3
        assert progress["completed"] == 2
        assert progress["by_status"][task_hub.TASK_STATUS_COMPLETED] == 2
        assert progress["by_status"][task_hub.TASK_STATUS_OPEN] == 1
    finally:
        conn.close()


def test_get_decomposition_tree_nests_children() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:tree-root")
        task_hub.decompose_task(
            conn,
            parent_task_id="task:tree-root",
            subtasks=[{"title": "Child A"}, {"title": "Child B"}],
        )
        # Nest under child A
        task_hub.decompose_task(
            conn,
            parent_task_id="task:tree-root:sub:1",
            subtasks=[{"title": "Grandchild A1"}],
        )

        tree = task_hub.get_decomposition_tree(conn, "task:tree-root", max_depth=2)
        assert tree["task_id"] == "task:tree-root"
        assert len(tree["subtasks"]) == 2
        child_a = tree["subtasks"][0]
        assert child_a["task_id"] == "task:tree-root:sub:1"
        assert len(child_a["subtasks"]) == 1
        assert child_a["subtasks"][0]["task_id"] == "task:tree-root:sub:1:sub:1"
    finally:
        conn.close()


def test_decomposition_tree_max_depth_limits_recursion() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:depth-root")
        task_hub.decompose_task(
            conn,
            parent_task_id="task:depth-root",
            subtasks=[{"title": "Child"}],
        )
        task_hub.decompose_task(
            conn,
            parent_task_id="task:depth-root:sub:1",
            subtasks=[{"title": "Grandchild"}],
        )

        tree = task_hub.get_decomposition_tree(conn, "task:depth-root", max_depth=1)
        # Depth 1 → children shown but not grandchildren
        assert len(tree["subtasks"]) == 1
        assert tree["subtasks"][0]["subtasks"] == []
    finally:
        conn.close()


def test_decompose_task_custom_task_ids() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:custom-parent")
        children = task_hub.decompose_task(
            conn,
            parent_task_id="task:custom-parent",
            subtasks=[
                {"task_id": "task:custom-child-alpha", "title": "Alpha"},
                {"title": "Auto-ID"},
            ],
        )
        assert children[0]["task_id"] == "task:custom-child-alpha"
        assert children[1]["task_id"] == "task:custom-parent:sub:2"
    finally:
        conn.close()


# ─── Refinement Lifecycle ────────────────────────────────────────────────────


def test_advance_refinement_through_stages() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:brainstorm")

        result = task_hub.advance_refinement(
            conn, task_id="task:brainstorm", new_stage="raw_idea",
        )
        assert result.get("refinement_stage") == "raw_idea"

        result = task_hub.advance_refinement(
            conn,
            task_id="task:brainstorm",
            new_stage="interviewing",
            context_update={"question_count": 3},
        )
        assert result.get("refinement_stage") == "interviewing"

        state = task_hub.get_refinement_state(conn, "task:brainstorm")
        assert state["refinement_stage"] == "interviewing"
        history = state["refinement_history"]
        assert len(history) == 2
        # Second entry should have context
        second_entry = list(history.values())[1]
        assert second_entry["context"]["question_count"] == 3
    finally:
        conn.close()


def test_advance_refinement_rejects_invalid_stage() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:bad-stage")
        try:
            task_hub.advance_refinement(
                conn, task_id="task:bad-stage", new_stage="nonexistent_stage",
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Invalid refinement stage" in str(e)
    finally:
        conn.close()


def test_advance_refinement_raises_on_missing_task() -> None:
    conn = _conn()
    try:
        try:
            task_hub.advance_refinement(
                conn, task_id="task:does-not-exist", new_stage="raw_idea",
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "not found" in str(e).lower()
    finally:
        conn.close()


def test_get_refinement_state_not_found() -> None:
    conn = _conn()
    try:
        state = task_hub.get_refinement_state(conn, "task:missing")
        assert state["error"] == "not_found"
    finally:
        conn.close()


# ─── trigger_type Column ─────────────────────────────────────────────────────


def test_trigger_type_defaults_to_heartbeat_poll() -> None:
    conn = _conn()
    try:
        item = _make_task(conn, "task:trigger-default")
        # trigger_type should default to heartbeat_poll
        retrieved = task_hub.get_item(conn, "task:trigger-default")
        assert retrieved is not None
        assert retrieved.get("trigger_type") == "heartbeat_poll"
    finally:
        conn.close()


def test_decomposed_subtasks_get_default_trigger_type() -> None:
    """trigger_type defaults to 'heartbeat_poll' via the SQL column DEFAULT.

    decompose_task now correctly passes through explicit trigger_type values.
    If omitted, upsert_item relies on the SQL schema default of 'heartbeat_poll'.
    """
    conn = _conn()
    try:
        _make_task(conn, "task:trigger-parent")
        children = task_hub.decompose_task(
            conn,
            parent_task_id="task:trigger-parent",
            subtasks=[
                {"title": "Immediate", "trigger_type": "immediate"},
                {"title": "Default"},
            ],
        )

        first = task_hub.get_item(conn, children[0]["task_id"])
        second = task_hub.get_item(conn, children[1]["task_id"])
        # Explicit trigger_type is preserved
        assert first is not None and first.get("trigger_type") == "immediate"
        # Omitted trigger_type falls back to SQL column default
        assert second is not None and second.get("trigger_type") == "heartbeat_poll"
    finally:
        conn.close()


# ─── parent_task_id Column ───────────────────────────────────────────────────


def test_parent_task_id_stored_on_upsert() -> None:
    conn = _conn()
    try:
        _make_task(conn, "task:child-direct", parent_task_id="task:my-parent")
        item = task_hub.get_item(conn, "task:child-direct")
        assert item is not None
        assert item.get("parent_task_id") == "task:my-parent"
    finally:
        conn.close()
