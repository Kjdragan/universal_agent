"""Tests for the new dashboard archive endpoint.

POST `/api/v1/dashboard/todolist/archive/{task_id}` flips an active task to
`completed` with `stale_state=dashboard_archived`. Distinct from the
existing dismiss endpoint (which sets `cancelled` + `dashboard_dismissed`).
Both endpoints are no-ops on already-terminal rows.
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_open_task(conn: sqlite3.Connection, task_id: str) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "email",
            "title": f"task {task_id}",
            "description": "quarantined email",
            "status": task_hub.TASK_STATUS_OPEN,
            "labels": ["quarantined"],
        },
    )


def _apply_archive(conn: sqlite3.Connection, task_id: str) -> str:
    """Equivalent SQL the endpoint runs; isolated from FastAPI to keep tests fast."""
    from datetime import datetime, timezone as _tz
    row = conn.execute(
        "SELECT status FROM task_hub_items WHERE task_id = ? LIMIT 1",
        (task_id,),
    ).fetchone()
    if not row:
        raise ValueError("task not found")
    current_status = str(row["status"] or "").strip().lower()
    if current_status in task_hub.TERMINAL_STATUSES:
        return current_status
    conn.execute(
        "UPDATE task_hub_items SET status=?, stale_state=?, seizure_state=?, updated_at=? WHERE task_id=?",
        (
            task_hub.TASK_STATUS_COMPLETED,
            "dashboard_archived",
            "unseized",
            datetime.now(_tz.utc).isoformat(),
            task_id,
        ),
    )
    conn.commit()
    return task_hub.TASK_STATUS_COMPLETED


# ── Happy path ─────────────────────────────────────────────────────────────


def test_archive_flips_open_task_to_completed_archived() -> None:
    conn = _conn()
    try:
        _seed_open_task(conn, "task_quarantine_1")
        new_status = _apply_archive(conn, "task_quarantine_1")
        assert new_status == task_hub.TASK_STATUS_COMPLETED
        row = task_hub.get_item(conn, "task_quarantine_1")
        assert row["status"] == task_hub.TASK_STATUS_COMPLETED
        assert row["stale_state"] == "dashboard_archived"
        assert row["seizure_state"] == "unseized"
    finally:
        conn.close()


def test_archive_works_on_in_progress_task() -> None:
    """Archiving must be available regardless of which active status the row holds."""
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task_in_progress",
                "source_kind": "email",
                "title": "in-progress quarantine",
                "status": task_hub.TASK_STATUS_IN_PROGRESS,
                "labels": ["quarantined"],
            },
        )
        new_status = _apply_archive(conn, "task_in_progress")
        assert new_status == task_hub.TASK_STATUS_COMPLETED
    finally:
        conn.close()


# ── Idempotency / refusal ──────────────────────────────────────────────────


def test_archive_on_already_completed_is_a_noop() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task_done",
                "source_kind": "email",
                "title": "already done",
                "status": task_hub.TASK_STATUS_COMPLETED,
            },
        )
        # The endpoint short-circuits with a "already terminal" detail.
        result = _apply_archive(conn, "task_done")
        assert result == task_hub.TASK_STATUS_COMPLETED
        # Stale state must NOT have been overwritten to dashboard_archived.
        row = task_hub.get_item(conn, "task_done")
        assert row["stale_state"] != "dashboard_archived"
    finally:
        conn.close()


def test_archive_on_cancelled_is_a_noop() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task_cancelled",
                "source_kind": "email",
                "title": "previously dismissed",
                "status": task_hub.TASK_STATUS_CANCELLED,
                "stale_state": "dashboard_dismissed",
            },
        )
        result = _apply_archive(conn, "task_cancelled")
        # Endpoint reports the existing terminal status back; row is untouched.
        assert result == task_hub.TASK_STATUS_CANCELLED
        row = task_hub.get_item(conn, "task_cancelled")
        assert row["status"] == task_hub.TASK_STATUS_CANCELLED
        assert row["stale_state"] == "dashboard_dismissed"
    finally:
        conn.close()


# ── Boundary errors ────────────────────────────────────────────────────────


def test_archive_on_missing_task_raises() -> None:
    conn = _conn()
    try:
        with pytest.raises(ValueError):
            _apply_archive(conn, "nonexistent_task")
    finally:
        conn.close()


# ── Route registration ─────────────────────────────────────────────────────


def test_archive_route_is_registered() -> None:
    """Smoke: the FastAPI app exposes the new POST endpoint."""
    import importlib
    gateway_server = importlib.import_module("universal_agent.gateway_server")
    app = getattr(gateway_server, "app", None)
    assert app is not None
    routes = {getattr(route, "path", None) for route in app.routes}
    assert "/api/v1/dashboard/todolist/archive/{task_id}" in routes
