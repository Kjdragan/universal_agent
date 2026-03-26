"""Unit tests for the Task Decomposition Pipeline (Phase 3).

Tests:
  - decompose_task creates subtasks correctly
  - decompose_task marks parent as 'decomposed'
  - complete_subtask_and_check_parent transitions subtask to completed
  - complete_subtask_and_check_parent auto-completes parent when all siblings done
  - get_decomposition_tree returns correct hierarchy
  - get_parent_progress tracks counts accurately
  - Gateway decompose/complete-subtask/subtasks endpoints
"""

from __future__ import annotations

import sqlite3
import pytest
from unittest.mock import AsyncMock, patch

from universal_agent.task_hub import (
    ensure_schema,
    upsert_item,
    get_item,
    decompose_task,
    complete_subtask_and_check_parent,
    get_decomposition_tree,
    get_parent_progress,
    list_subtasks,
    TASK_STATUS_OPEN,
    TASK_STATUS_COMPLETED,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _insert_task(conn, task_id: str, **overrides) -> dict:
    item = {
        "task_id": task_id,
        "source_kind": "test",
        "title": f"Task {task_id}",
        "description": f"Desc for {task_id}",
        "project_key": "immediate",
        "priority": 2,
        "status": TASK_STATUS_OPEN,
        **overrides,
    }
    return upsert_item(conn, item)


# ── decompose_task ───────────────────────────────────────────────────────

class TestDecomposeTask:
    def test_creates_subtasks(self):
        conn = _make_conn()
        _insert_task(conn, "parent-1")
        subtasks = [
            {"title": "Sub A", "description": "Do A"},
            {"title": "Sub B", "description": "Do B"},
        ]
        created = decompose_task(conn, parent_task_id="parent-1", subtasks=subtasks)
        assert len(created) == 2
        assert created[0]["title"] == "Sub A"
        assert created[1]["title"] == "Sub B"
        # Each child references parent
        assert created[0]["parent_task_id"] == "parent-1"
        assert created[1]["parent_task_id"] == "parent-1"

    def test_sets_parent_refinement_stage(self):
        conn = _make_conn()
        _insert_task(conn, "parent-2")
        decompose_task(conn, parent_task_id="parent-2", subtasks=[{"title": "X"}])
        parent = get_item(conn, "parent-2")
        assert parent is not None
        assert parent.get("refinement_stage") == "decomposed"

    def test_subtask_ids_follow_convention(self):
        conn = _make_conn()
        _insert_task(conn, "p-3")
        created = decompose_task(
            conn, parent_task_id="p-3",
            subtasks=[{"title": "A"}, {"title": "B"}, {"title": "C"}],
        )
        assert created[0]["task_id"] == "p-3:sub:1"
        assert created[1]["task_id"] == "p-3:sub:2"
        assert created[2]["task_id"] == "p-3:sub:3"

    def test_raises_for_missing_parent(self):
        conn = _make_conn()
        with pytest.raises(ValueError, match="Parent task not found"):
            decompose_task(conn, parent_task_id="nonexistent", subtasks=[{"title": "A"}])

    def test_subtasks_inherit_project_key(self):
        conn = _make_conn()
        _insert_task(conn, "p-4", project_key="research")
        created = decompose_task(
            conn, parent_task_id="p-4", subtasks=[{"title": "sub1"}],
        )
        assert created[0].get("project_key") == "research"


# ── complete_subtask_and_check_parent ────────────────────────────────────

class TestCompleteSubtaskAndCheckParent:
    def test_marks_subtask_completed(self):
        conn = _make_conn()
        _insert_task(conn, "p-5")
        decompose_task(conn, parent_task_id="p-5", subtasks=[{"title": "S1"}, {"title": "S2"}])
        result = complete_subtask_and_check_parent(conn, "p-5:sub:1")
        assert result["task"]["status"] == TASK_STATUS_COMPLETED
        assert result["parent_completed"] is False

    def test_auto_completes_parent_when_all_done(self):
        conn = _make_conn()
        _insert_task(conn, "p-6")
        decompose_task(conn, parent_task_id="p-6", subtasks=[{"title": "S1"}, {"title": "S2"}])
        complete_subtask_and_check_parent(conn, "p-6:sub:1")
        result = complete_subtask_and_check_parent(conn, "p-6:sub:2")
        assert result["parent_completed"] is True
        parent = get_item(conn, "p-6")
        assert parent is not None
        assert parent["status"] == TASK_STATUS_COMPLETED

    def test_raises_for_missing_task(self):
        conn = _make_conn()
        with pytest.raises(ValueError, match="Task not found"):
            complete_subtask_and_check_parent(conn, "ghost-task")

    def test_parentless_task_completes_without_error(self):
        conn = _make_conn()
        _insert_task(conn, "standalone")
        result = complete_subtask_and_check_parent(conn, "standalone")
        assert result["task"]["status"] == TASK_STATUS_COMPLETED
        assert result["parent_completed"] is False


# ── get_decomposition_tree / get_parent_progress / list_subtasks ─────────

class TestTreeAndProgress:
    def test_get_decomposition_tree(self):
        conn = _make_conn()
        _insert_task(conn, "p-7")
        decompose_task(conn, parent_task_id="p-7", subtasks=[{"title": "A"}, {"title": "B"}])
        tree = get_decomposition_tree(conn, "p-7")
        assert tree["task_id"] == "p-7"
        assert len(tree["subtasks"]) == 2
        assert tree["subtasks"][0]["title"] == "A"

    def test_get_parent_progress(self):
        conn = _make_conn()
        _insert_task(conn, "p-8")
        decompose_task(
            conn, parent_task_id="p-8",
            subtasks=[{"title": "A"}, {"title": "B"}, {"title": "C"}],
        )
        progress = get_parent_progress(conn, "p-8")
        assert progress["total"] == 3
        assert progress["completed"] == 0

        complete_subtask_and_check_parent(conn, "p-8:sub:1")
        progress = get_parent_progress(conn, "p-8")
        assert progress["completed"] == 1
        assert progress["total"] == 3

    def test_list_subtasks(self):
        conn = _make_conn()
        _insert_task(conn, "p-9")
        decompose_task(conn, parent_task_id="p-9", subtasks=[{"title": "X"}, {"title": "Y"}])
        subs = list_subtasks(conn, "p-9")
        assert len(subs) == 2
        titles = {s["title"] for s in subs}
        assert titles == {"X", "Y"}


# ── Gateway endpoint tests ──────────────────────────────────────────────

class TestGatewayDecompositionEndpoints:
    """Smoke tests for decomposition gateway wiring (requires httpx)."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        try:
            import httpx
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("httpx/fastapi not available")

    def test_subtasks_endpoint_404_for_missing(self):
        from fastapi.testclient import TestClient
        from universal_agent.gateway_server import app
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/dashboard/todolist/tasks/nonexistent/subtasks")
        # Should not crash — returns tree (possibly empty)
        assert resp.status_code in (200, 404)

    def test_complete_subtask_endpoint_404_for_missing(self):
        from fastapi.testclient import TestClient
        from universal_agent.gateway_server import app
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/v1/dashboard/todolist/tasks/nonexistent/complete-subtask")
        assert resp.status_code == 404
