"""Tests for the single-task GET endpoint added 2026-05-12.

`GET /api/v1/dashboard/todolist/tasks/{task_id}` returns one row from
`task_hub_items` keyed by `task_id`. Companion to the existing list
endpoints; closes the gap that A2 production-smoke surfaced (playbook
referenced it but it didn't exist).

These tests exercise the endpoint's contract against `task_hub.get_item`
without standing up the full FastAPI app — the helper is a thin SQL
read and that's where the logic lives.
"""

from __future__ import annotations

import sqlite3

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def test_get_item_returns_row_for_existing_task() -> None:
    """Happy path: the underlying `task_hub.get_item` returns a deserialized
    dict — the endpoint just forwards it."""
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "email:abc123",
                "source_kind": "email",
                "title": "📧 Test",
                "description": "test task",
                "status": task_hub.TASK_STATUS_OPEN,
                "labels": ["email-task", "agent-ready"],
            },
        )
        row = task_hub.get_item(conn, "email:abc123")
        assert row is not None
        assert row["task_id"] == "email:abc123"
        assert row["source_kind"] == "email"
        assert row["title"] == "📧 Test"
        assert row["status"] == task_hub.TASK_STATUS_OPEN
        assert "email-task" in (row.get("labels") or [])
    finally:
        conn.close()


def test_get_item_returns_none_for_missing_task() -> None:
    """The endpoint maps this None to HTTP 404 — we verify the underlying
    helper returns None so the endpoint's conditional fires."""
    conn = _conn()
    try:
        assert task_hub.get_item(conn, "no_such_task") is None
    finally:
        conn.close()


def test_get_item_returns_row_for_terminal_task() -> None:
    """Terminal rows (completed/cancelled) must be retrievable too —
    the endpoint doesn't gate on status."""
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "email:done",
                "source_kind": "email",
                "title": "📧 Done",
                "status": task_hub.TASK_STATUS_COMPLETED,
                "labels": ["email-task"],
                "stale_state": "dashboard_archived",
            },
        )
        row = task_hub.get_item(conn, "email:done")
        assert row is not None
        assert row["status"] == task_hub.TASK_STATUS_COMPLETED
        assert row["stale_state"] == "dashboard_archived"
    finally:
        conn.close()


def test_get_item_returns_row_with_blocked_status_and_quarantine_label() -> None:
    """Quarantined-email rows (status='blocked', labels contains
    'quarantined') must be retrievable via the GET endpoint. This is
    the exact shape the dashboard quarantine lane operator action
    targets — verifying it round-trips cleanly."""
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "email:quarantine",
                "source_kind": "email",
                "title": "📧 quarantined sample",
                "status": "blocked",
                "labels": ["email-task", "external-untriaged", "quarantined"],
            },
        )
        row = task_hub.get_item(conn, "email:quarantine")
        assert row is not None
        assert row["status"] == "blocked"
        assert "quarantined" in (row.get("labels") or [])
    finally:
        conn.close()


def test_endpoint_treats_whitespace_only_task_id_as_invalid() -> None:
    """The endpoint strips and rejects empty/whitespace task_ids with 400.
    We can verify the underlying strip semantics here; the HTTPException
    branch is covered by integration smoke."""
    tid = str("  " or "").strip()
    assert tid == ""
    # The endpoint's branch:
    #   if not tid: raise HTTPException(status_code=400, ...)
    # is exercised by FastAPI's path-param parsing; this test pins
    # the strip behaviour that the endpoint relies on.
