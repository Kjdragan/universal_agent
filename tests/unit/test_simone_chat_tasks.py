"""Unit tests for the Simone chat → Task Hub lifecycle bridge.

Covers:
* First operator message creates an `in_progress` row with the right shape.
* The row write is idempotent (second call returns the existing row).
* `query_complete` sets `completion_proposed_at` but does NOT change status.
* New operator message on a `completed` row flips back to `in_progress`
  and clears `completion_proposed_at`.
* `auto_complete_stale` promotes eligible rows and ignores ones where the
  operator replied after Simone proposed done.
* Dispatch sweep does not claim `simone_chat` rows (because status = in_progress).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services import simone_chat_tasks


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _get_metadata(conn: sqlite3.Connection, task_id: str) -> dict:
    row = task_hub.get_item(conn, task_id)
    assert row is not None
    return dict(row.get("metadata") or {})


# ── Creation ────────────────────────────────────────────────────────────────


def test_first_operator_message_creates_in_progress_row() -> None:
    conn = _conn()
    try:
        row = simone_chat_tasks.on_operator_message(
            conn,
            session_id="sess_abc",
            text="archive the Tokenrip quarantine email",
            source_page="/dashboard/mission-control",
        )
        assert row is not None
        assert row["task_id"] == "simone_chat:sess_abc"
        assert row["source_kind"] == "simone_chat"
        assert row["source_ref"] == "sess_abc"
        assert row["status"] == task_hub.TASK_STATUS_IN_PROGRESS
        assert "archive the Tokenrip" in row["title"]
        metadata = dict(row.get("metadata") or {})
        assert metadata["session_id"] == "sess_abc"
        assert metadata["source_page"] == "/dashboard/mission-control"
        assert metadata.get("last_operator_message_at")
        assert metadata.get("started_at")
        assert metadata.get("completion_proposed_at") is None
    finally:
        conn.close()


def test_record_first_operator_message_is_idempotent() -> None:
    conn = _conn()
    try:
        first = simone_chat_tasks.record_first_operator_message(
            conn,
            session_id="sess_idem",
            first_message="hello",
        )
        assert first["status"] == task_hub.TASK_STATUS_IN_PROGRESS
        original_title = first["title"]
        # Second call with a different message must NOT overwrite the existing row.
        second = simone_chat_tasks.record_first_operator_message(
            conn,
            session_id="sess_idem",
            first_message="completely different message",
        )
        assert second["task_id"] == first["task_id"]
        assert second["title"] == original_title
    finally:
        conn.close()


def test_empty_operator_message_is_noop() -> None:
    conn = _conn()
    try:
        result = simone_chat_tasks.on_operator_message(
            conn, session_id="sess_x", text="   "
        )
        assert result is None
        assert task_hub.get_item(conn, "simone_chat:sess_x") is None
    finally:
        conn.close()


# ── Completion proposal ─────────────────────────────────────────────────────


def test_query_complete_sets_proposed_without_changing_status() -> None:
    conn = _conn()
    try:
        simone_chat_tasks.on_operator_message(
            conn, session_id="sess_done", text="status?"
        )
        result = simone_chat_tasks.on_query_complete(
            conn, session_id="sess_done", completed=True
        )
        assert result is not None
        assert result["status"] == task_hub.TASK_STATUS_IN_PROGRESS
        metadata = _get_metadata(conn, "simone_chat:sess_done")
        assert metadata.get("completion_proposed_at")
    finally:
        conn.close()


def test_query_complete_false_is_noop() -> None:
    conn = _conn()
    try:
        simone_chat_tasks.on_operator_message(
            conn, session_id="sess_partial", text="something"
        )
        result = simone_chat_tasks.on_query_complete(
            conn, session_id="sess_partial", completed=False
        )
        assert result is None
        metadata = _get_metadata(conn, "simone_chat:sess_partial")
        assert metadata.get("completion_proposed_at") is None
    finally:
        conn.close()


def test_query_complete_before_first_message_is_noop() -> None:
    conn = _conn()
    try:
        result = simone_chat_tasks.on_query_complete(
            conn, session_id="sess_ghost", completed=True
        )
        assert result is None
    finally:
        conn.close()


# ── Manual completion + reopen ──────────────────────────────────────────────


def test_mark_complete_flips_status() -> None:
    conn = _conn()
    try:
        simone_chat_tasks.on_operator_message(
            conn, session_id="sess_mc", text="foo"
        )
        row = simone_chat_tasks.mark_complete(conn, session_id="sess_mc")
        assert row is not None
        assert row["status"] == task_hub.TASK_STATUS_COMPLETED
        metadata = _get_metadata(conn, "simone_chat:sess_mc")
        assert metadata.get("completed_at")
    finally:
        conn.close()


def test_mark_complete_on_terminal_row_is_noop() -> None:
    conn = _conn()
    try:
        simone_chat_tasks.on_operator_message(
            conn, session_id="sess_term", text="x"
        )
        simone_chat_tasks.mark_complete(conn, session_id="sess_term")
        # Second call must not raise and must keep status terminal.
        row = simone_chat_tasks.mark_complete(conn, session_id="sess_term")
        assert row is not None
        assert row["status"] == task_hub.TASK_STATUS_COMPLETED
    finally:
        conn.close()


def test_reopen_flips_completed_back_to_in_progress() -> None:
    conn = _conn()
    try:
        simone_chat_tasks.on_operator_message(
            conn, session_id="sess_re", text="x"
        )
        simone_chat_tasks.mark_complete(conn, session_id="sess_re")
        row = simone_chat_tasks.reopen(conn, task_id="simone_chat:sess_re")
        assert row is not None
        assert row["status"] == task_hub.TASK_STATUS_IN_PROGRESS
        metadata = _get_metadata(conn, "simone_chat:sess_re")
        assert metadata.get("completion_proposed_at") is None
        assert metadata.get("reopened_at")
    finally:
        conn.close()


# ── Resume on new operator message ──────────────────────────────────────────


def test_new_operator_message_on_completed_row_resumes() -> None:
    conn = _conn()
    try:
        simone_chat_tasks.on_operator_message(
            conn, session_id="sess_resume", text="initial"
        )
        simone_chat_tasks.on_query_complete(
            conn, session_id="sess_resume", completed=True
        )
        simone_chat_tasks.mark_complete(conn, session_id="sess_resume")
        # New message arrives after completion → row must flip back.
        result = simone_chat_tasks.on_operator_message(
            conn, session_id="sess_resume", text="oh wait, follow-up"
        )
        assert result is not None
        assert result["status"] == task_hub.TASK_STATUS_IN_PROGRESS
        metadata = _get_metadata(conn, "simone_chat:sess_resume")
        assert metadata.get("completion_proposed_at") is None
        assert metadata.get("resumed_at")
    finally:
        conn.close()


# ── Auto-completion cron ────────────────────────────────────────────────────


def test_auto_complete_promotes_stale_proposed_rows() -> None:
    conn = _conn()
    try:
        simone_chat_tasks.on_operator_message(
            conn, session_id="sess_stale", text="status?"
        )
        # Backdate BOTH the proposal AND the last-operator-message timestamp
        # so the row matches "Simone proposed done, operator never replied".
        # auto_complete_stale deliberately skips rows where last_msg > proposed
        # (operator replied after Simone proposed — conversation isn't over).
        old_iso = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        task_hub.upsert_item(
            conn,
            {
                "task_id": "simone_chat:sess_stale",
                "metadata": {
                    "completion_proposed_at": old_iso,
                    "last_operator_message_at": old_iso,
                },
            },
        )
        promoted = simone_chat_tasks.auto_complete_stale(
            conn, idle_threshold_minutes=10
        )
        assert promoted == ["simone_chat:sess_stale"]
        row = task_hub.get_item(conn, "simone_chat:sess_stale")
        assert row["status"] == task_hub.TASK_STATUS_COMPLETED
    finally:
        conn.close()


def test_auto_complete_skips_when_operator_replied_after_proposal() -> None:
    """If operator replied AFTER Simone proposed done, the conversation isn't over."""
    conn = _conn()
    try:
        simone_chat_tasks.on_operator_message(
            conn, session_id="sess_active", text="status?"
        )
        old_proposal = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        recent_reply = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        task_hub.upsert_item(
            conn,
            {
                "task_id": "simone_chat:sess_active",
                "metadata": {
                    "completion_proposed_at": old_proposal,
                    "last_operator_message_at": recent_reply,
                },
            },
        )
        promoted = simone_chat_tasks.auto_complete_stale(
            conn, idle_threshold_minutes=10
        )
        assert promoted == []
        row = task_hub.get_item(conn, "simone_chat:sess_active")
        assert row["status"] == task_hub.TASK_STATUS_IN_PROGRESS
    finally:
        conn.close()


def test_auto_complete_skips_rows_without_proposal() -> None:
    conn = _conn()
    try:
        simone_chat_tasks.on_operator_message(
            conn, session_id="sess_no_prop", text="x"
        )
        promoted = simone_chat_tasks.auto_complete_stale(
            conn, idle_threshold_minutes=10
        )
        assert promoted == []
    finally:
        conn.close()


def test_auto_complete_skips_recent_proposals() -> None:
    conn = _conn()
    try:
        simone_chat_tasks.on_operator_message(
            conn, session_id="sess_fresh_prop", text="x"
        )
        simone_chat_tasks.on_query_complete(
            conn, session_id="sess_fresh_prop", completed=True
        )
        # Proposal was just now; should not auto-complete yet.
        promoted = simone_chat_tasks.auto_complete_stale(
            conn, idle_threshold_minutes=10
        )
        assert promoted == []
    finally:
        conn.close()


# ── Dispatch isolation ──────────────────────────────────────────────────────


def test_dispatch_sweep_does_not_claim_simone_chat_rows() -> None:
    """`claim_next_dispatch_tasks` filters status IN ('open', 'needs_review').

    Rows we write are `in_progress` from the start, so they should never appear
    in dispatch results regardless of the rebuild pipeline.
    """
    conn = _conn()
    try:
        simone_chat_tasks.on_operator_message(
            conn, session_id="sess_dispatch", text="x"
        )
        # Re-build the dispatch queue. If the chat row leaked into the
        # eligible queue we'd see it surface with claim_next_dispatch_tasks.
        task_hub.rebuild_dispatch_queue(conn)
        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=10)
        chat_claims = [c for c in claimed if str(c.get("task_id", "")).startswith("simone_chat:")]
        assert chat_claims == []
    finally:
        conn.close()
