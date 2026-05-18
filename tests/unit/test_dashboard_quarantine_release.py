"""Tests for the quarantine-release wiring on dashboard archive/dismiss.

When the operator clicks "Archive" or "Delete" on a quarantined-email card,
the Task Hub row flips to a terminal status (existing behaviour). This file
covers the follow-up cleanup added on top of that:

  1. `EmailTaskBridge.clear_quarantine_state(task_id)` strips the
     `quarantined` label from `task_hub_items.labels`, flips
     `email_task_mappings.status` from `quarantined` to `released`,
     and returns the thread_id for the AgentMail call.
  2. `gateway_server._clear_email_quarantine_local(conn, task_id)` is a
     no-op on non-email rows / non-quarantined rows.
  3. `gateway_server._release_quarantine_agentmail(thread_id, task_id)`
     is fire-and-log: it never raises even if the AgentMail SDK does.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from universal_agent import task_hub
from universal_agent.services.email_task_bridge import (
    EmailTaskBridge,
    _deterministic_task_id,
    ensure_email_task_schema,
)

# ── Helpers ───────────────────────────────────────────────────────────────


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    ensure_email_task_schema(conn)
    return conn


def _seed_quarantined_email(
    conn: sqlite3.Connection,
    *,
    thread_id: str = "thread_abc",
    sender: str = "spammer@example.test",
) -> str:
    """Seed a quarantined email task: Task Hub row + bridge mapping. Returns task_id."""
    task_id = _deterministic_task_id(thread_id)
    now = datetime.now(timezone.utc).isoformat()

    # ── 1. Bridge mapping row ──
    conn.execute(
        """
        INSERT INTO email_task_mappings (
            thread_id, task_id, master_key, subject, sender_email,
            sender_trusted, security_classification, status,
            last_message_id, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 0, 'quarantine', 'quarantined', ?, ?, ?)
        """,
        (thread_id, task_id, "k1", "Hello", sender, "msg_1", now, now),
    )
    conn.commit()

    # ── 2. Task Hub row ──
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "email",
            "title": "📧 Hello",
            "description": "quarantined email",
            "status": task_hub.TASK_STATUS_OPEN,
            "labels": ["email-task", "external-untriaged", "quarantined"],
        },
    )
    return task_id


# ── EmailTaskBridge.clear_quarantine_state ───────────────────────────────


def test_clear_quarantine_state_strips_label_and_updates_mapping() -> None:
    """Happy path: a quarantined email is fully scrubbed on both tables."""
    conn = _conn()
    try:
        task_id = _seed_quarantined_email(conn, thread_id="t_clear")
        bridge = EmailTaskBridge(db_conn=conn)

        result = bridge.clear_quarantine_state(task_id)

        # Returns thread_id so the dashboard can fire the AgentMail call.
        assert result == "t_clear"

        # Bridge mapping flipped from `quarantined` to `released`.
        row = conn.execute(
            "SELECT status, security_classification FROM email_task_mappings "
            "WHERE task_id = ? LIMIT 1",
            (task_id,),
        ).fetchone()
        assert row["status"] == "released"
        # security_classification is blanked so future re-derivation
        # doesn't mistake this as "still being held for review."
        assert row["security_classification"] == ""

        # Task Hub label list no longer contains "quarantined" — the
        # dashboard badge keys off this exact field.
        item = task_hub.get_item(conn, task_id)
        assert "quarantined" not in (item.get("labels") or [])
        # Non-quarantine labels are preserved.
        assert "email-task" in (item.get("labels") or [])
    finally:
        conn.close()


def test_clear_quarantine_state_is_idempotent() -> None:
    """Calling clear twice doesn't error and the second call is a no-op."""
    conn = _conn()
    try:
        task_id = _seed_quarantined_email(conn, thread_id="t_idem")
        bridge = EmailTaskBridge(db_conn=conn)
        bridge.clear_quarantine_state(task_id)
        # Second call: mapping is now `released`, label is gone; should be safe.
        result = bridge.clear_quarantine_state(task_id)
        # Returns thread_id (mapping still exists) but does no harm.
        assert result == "t_idem"

        row = conn.execute(
            "SELECT status FROM email_task_mappings WHERE task_id = ? LIMIT 1",
            (task_id,),
        ).fetchone()
        assert row["status"] == "released"
    finally:
        conn.close()


def test_clear_quarantine_state_returns_none_when_task_id_unknown() -> None:
    """Non-email or never-seen task_id is a clean None — caller short-circuits."""
    conn = _conn()
    try:
        bridge = EmailTaskBridge(db_conn=conn)
        assert bridge.clear_quarantine_state("no_such_task") is None
    finally:
        conn.close()


# ── gateway_server._clear_email_quarantine_local ──────────────────────────


def test_clear_email_quarantine_local_returns_thread_id_for_quarantined_email() -> None:
    """Sync helper used by the dashboard handlers returns the thread_id."""
    from universal_agent.gateway_server import _clear_email_quarantine_local

    conn = _conn()
    try:
        task_id = _seed_quarantined_email(conn, thread_id="t_gw_email")
        out = _clear_email_quarantine_local(conn, task_id)
        assert out == "t_gw_email"
    finally:
        conn.close()


def test_clear_email_quarantine_local_returns_none_for_non_email_task() -> None:
    """A non-email task — even if it has a stray 'quarantined' label — is not touched."""
    from universal_agent.gateway_server import _clear_email_quarantine_local

    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task_simone_chat_1",
                "source_kind": "simone_chat",
                "title": "chat",
                "status": task_hub.TASK_STATUS_IN_PROGRESS,
                "labels": ["simone-chat", "quarantined"],  # contrived
            },
        )
        out = _clear_email_quarantine_local(conn, "task_simone_chat_1")
        assert out is None
    finally:
        conn.close()


def test_clear_email_quarantine_local_returns_none_for_email_without_quarantined_label() -> None:
    """An ordinary (non-quarantined) email is left alone — only quarantined rows trigger cleanup."""
    from universal_agent.gateway_server import _clear_email_quarantine_local

    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task_email_normal",
                "source_kind": "email",
                "title": "normal mail",
                "status": task_hub.TASK_STATUS_OPEN,
                "labels": ["email-task", "agent-ready"],
            },
        )
        out = _clear_email_quarantine_local(conn, "task_email_normal")
        assert out is None
    finally:
        conn.close()


# ── gateway_server._release_quarantine_agentmail ──────────────────────────


def test_release_quarantine_agentmail_is_noop_when_thread_id_missing() -> None:
    """No thread_id (sync helper returned None) → no AgentMail call attempted."""
    from universal_agent import gateway_server

    fake_service = MagicMock()
    fake_service.release_quarantine_label = AsyncMock(return_value=True)
    gateway_server._agentmail_service = fake_service
    try:
        asyncio.run(gateway_server._release_quarantine_agentmail(None, "task_1"))
        fake_service.release_quarantine_label.assert_not_called()
    finally:
        gateway_server._agentmail_service = None


def test_release_quarantine_agentmail_is_noop_when_service_none() -> None:
    """AgentMail service not initialised → log + return; no crash."""
    from universal_agent import gateway_server

    prev = gateway_server._agentmail_service
    gateway_server._agentmail_service = None
    try:
        # Should NOT raise.
        asyncio.run(gateway_server._release_quarantine_agentmail("thread_x", "task_x"))
    finally:
        gateway_server._agentmail_service = prev


def test_release_quarantine_agentmail_calls_service_with_thread_id() -> None:
    """Happy path: thread_id + service present → AgentMail helper invoked."""
    from universal_agent import gateway_server

    fake_service = MagicMock()
    fake_service.release_quarantine_label = AsyncMock(return_value=True)
    gateway_server._agentmail_service = fake_service
    try:
        asyncio.run(
            gateway_server._release_quarantine_agentmail("thread_happy", "task_happy")
        )
        fake_service.release_quarantine_label.assert_awaited_once_with("thread_happy")
    finally:
        gateway_server._agentmail_service = None


def test_release_quarantine_agentmail_swallows_service_exception() -> None:
    """Fire-and-log: AgentMail raising must NOT propagate (operator already saw row move)."""
    from universal_agent import gateway_server

    fake_service = MagicMock()
    fake_service.release_quarantine_label = AsyncMock(
        side_effect=RuntimeError("agentmail API down"),
    )
    gateway_server._agentmail_service = fake_service
    try:
        # Should NOT raise — log only.
        asyncio.run(
            gateway_server._release_quarantine_agentmail("thread_dead", "task_dead")
        )
        fake_service.release_quarantine_label.assert_awaited_once()
    finally:
        gateway_server._agentmail_service = None
