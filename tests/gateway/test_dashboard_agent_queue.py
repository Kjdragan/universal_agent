"""Tests for the /api/v1/dashboard/todolist/agent-queue endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from universal_agent import gateway_server
from universal_agent import task_hub


def _seed_task(
    conn,
    *,
    task_id: str | None = None,
    title: str = "Test task",
    status: str = "open",
    priority: int = 1,
    project_key: str = "immediate",
    labels: list[str] | None = None,
    must_complete: bool = False,
    incident_key: str | None = None,
    due_at: str | None = None,
    source_kind: str = "manual",
):
    """Insert a single task into task_hub_items."""
    import json

    tid = task_id or f"task-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO task_hub_items "
        "(task_id, source_kind, source_ref, title, description, project_key, priority, "
        "due_at, labels_json, status, must_complete, incident_key, agent_ready, score, "
        "score_confidence, stale_state, seizure_state, mirror_status, metadata_json, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            tid,
            source_kind,
            None,
            title,
            "",
            project_key,
            priority,
            due_at or None,
            json.dumps(labels or []),
            status,
            int(must_complete),
            incident_key or None,
            0,
            0.0,
            0.0,
            "fresh",
            "unseized",
            "internal",
            "{}",
            now_iso,
            now_iso,
        ),
    )
    conn.commit()
    return tid


@pytest.mark.asyncio
async def test_agent_queue_empty_database(monkeypatch, tmp_path):
    """Endpoint returns empty items list when database has no tasks."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    response = await gateway_server.dashboard_todolist_agent_queue(
        offset=0,
        limit=10,
        status="pending",
    )

    assert response["status"] == "ok"
    assert response["items"] == []
    assert response["pagination"]["total"] == 0
    assert response["pagination"]["count"] == 0
    assert response["pagination"]["has_more"] is False
    assert response["pagination"]["offset"] == 0
    assert response["pagination"]["limit"] == 10


@pytest.mark.asyncio
async def test_agent_queue_status_filter_returns_seeded_tasks(monkeypatch, tmp_path):
    """Status filter returns seeded tasks with correct shape."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            _seed_task(conn, task_id="tq-001", title="Task one", status="open", priority=1)
            _seed_task(conn, task_id="tq-002", title="Task two", status="in_progress", priority=2)
            _seed_task(conn, task_id="tq-003", title="Task three", status="blocked", priority=3)
            _seed_task(conn, task_id="tq-004", title="Task four", status="completed", priority=1)
        finally:
            conn.close()

    response = await gateway_server.dashboard_todolist_agent_queue(
        offset=0, limit=10, status="pending",
    )

    assert response["status"] == "ok"
    assert response["pagination"]["total"] == 3
    assert response["pagination"]["count"] == 3
    assert len(response["items"]) == 3

    task_ids = {item["task_id"] for item in response["items"]}
    assert task_ids == {"tq-001", "tq-002", "tq-003"}
    assert "tq-004" not in task_ids  # completed, not pending


@pytest.mark.asyncio
async def test_agent_queue_filter_by_status_completed(monkeypatch, tmp_path):
    """Filtering by status=completed returns only completed tasks."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            _seed_task(conn, task_id="tq-open", title="Open task", status="open")
            _seed_task(conn, task_id="tq-done", title="Done task", status="completed")
        finally:
            conn.close()

    response = await gateway_server.dashboard_todolist_agent_queue(
        offset=0, limit=10, status="completed",
    )

    assert response["status"] == "ok"
    assert response["pagination"]["total"] == 1
    assert response["items"][0]["task_id"] == "tq-done"


@pytest.mark.asyncio
async def test_agent_queue_filter_by_single_status(monkeypatch, tmp_path):
    """Filtering by a specific status (e.g. in_progress) returns only matching tasks."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            _seed_task(conn, task_id="tq-open", title="Open task", status="open")
            _seed_task(conn, task_id="tq-prog", title="In progress", status="in_progress")
            _seed_task(conn, task_id="tq-blocked", title="Blocked", status="blocked")
        finally:
            conn.close()

    response = await gateway_server.dashboard_todolist_agent_queue(
        offset=0, limit=10, status="in_progress",
    )

    assert response["status"] == "ok"
    assert response["pagination"]["total"] == 1
    assert response["items"][0]["task_id"] == "tq-prog"


@pytest.mark.asyncio
async def test_agent_queue_pagination(monkeypatch, tmp_path):
    """Pagination works correctly with offset and limit."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            for i in range(5):
                _seed_task(conn, task_id=f"tq-page-{i}", title=f"Task {i}", status="open")
        finally:
            conn.close()

    # Page 1: offset=0, limit=2
    response = await gateway_server.dashboard_todolist_agent_queue(
        offset=0, limit=2, status="open",
    )
    assert response["pagination"]["total"] == 5
    assert response["pagination"]["count"] == 2
    assert response["pagination"]["has_more"] is True
    assert response["pagination"]["offset"] == 0
    assert response["pagination"]["limit"] == 2

    # Page 2: offset=2, limit=2
    response = await gateway_server.dashboard_todolist_agent_queue(
        offset=2, limit=2, status="open",
    )
    assert response["pagination"]["count"] == 2
    assert response["pagination"]["has_more"] is True

    # Page 3: offset=4, limit=2
    response = await gateway_server.dashboard_todolist_agent_queue(
        offset=4, limit=2, status="open",
    )
    assert response["pagination"]["count"] == 1
    assert response["pagination"]["has_more"] is False


@pytest.mark.asyncio
async def test_agent_queue_item_shape(monkeypatch, tmp_path):
    """Each item in the response matches the AgentQueueItem schema."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            _seed_task(
                conn,
                task_id="tq-shape",
                title="Shape test",
                status="open",
                priority=2,
                project_key="research",
                labels=["research", "test"],
                must_complete=True,
                incident_key=None,
                due_at="2026-04-01T00:00:00Z",
                source_kind="csi",
            )
        finally:
            conn.close()

    response = await gateway_server.dashboard_todolist_agent_queue(
        offset=0, limit=10, status="open",
    )

    assert response["status"] == "ok"
    assert len(response["items"]) == 1
    item = response["items"][0]

    assert item["task_id"] == "tq-shape"
    assert item["title"] == "Shape test"
    assert item["project_key"] == "research"
    assert item["priority"] == 2
    assert set(item["labels"]) == {"research", "test"}
    assert item["status"] == "open"
    assert item["must_complete"] is True
    assert item["incident_key"] is None
    assert isinstance(item["score"], float)
    assert item["due_at"] == "2026-04-01T00:00:00Z"
    assert item["source_kind"] == "csi"
    assert "updated_at" in item


@pytest.mark.asyncio
async def test_agent_queue_limit_clamped(monkeypatch, tmp_path):
    """Limit is clamped to max 100."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    response = await gateway_server.dashboard_todolist_agent_queue(
        offset=0, limit=500, status="pending",
    )

    assert response["pagination"]["limit"] == 100


@pytest.mark.asyncio
async def test_agent_queue_offset_clamped(monkeypatch, tmp_path):
    """Negative offset is clamped to 0."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    response = await gateway_server.dashboard_todolist_agent_queue(
        offset=-5, limit=10, status="pending",
    )

    assert response["pagination"]["offset"] == 0


@pytest.mark.asyncio
async def test_agent_queue_backward_compat_default_all(monkeypatch, tmp_path):
    """Calling without status param falls through to existing list_agent_queue."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    # This should use the default path (list_agent_queue) and not error
    response = await gateway_server.dashboard_todolist_agent_queue(
        offset=0, limit=10,
    )

    assert response["status"] == "ok"
    assert "items" in response
    assert "pagination" in response


@pytest.mark.asyncio
async def test_agent_queue_incident_key_present(monkeypatch, tmp_path):
    """incident_key is included when present on the task."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            _seed_task(
                conn,
                task_id="tq-inc",
                title="Incident task",
                status="open",
                incident_key="INC-123",
            )
        finally:
            conn.close()

    response = await gateway_server.dashboard_todolist_agent_queue(
        offset=0, limit=10, status="open",
    )

    assert response["status"] == "ok"
    assert response["items"][0]["incident_key"] == "INC-123"


@pytest.mark.asyncio
async def test_agent_queue_description_null_when_empty(monkeypatch, tmp_path):
    """description is null when the task has no description."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            _seed_task(conn, task_id="tq-desc", title="No desc", status="open")
        finally:
            conn.close()

    response = await gateway_server.dashboard_todolist_agent_queue(
        offset=0, limit=10, status="open",
    )

    # description column defaults to '' in the schema; hydrate_item preserves it.
    # The endpoint maps empty string to None.
    assert response["items"][0]["description"] is None
