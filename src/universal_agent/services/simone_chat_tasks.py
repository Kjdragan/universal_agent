"""Simone chat → Task Hub lifecycle bridge.

Every interactive Simone chat session is represented as a Task Hub row so
operators see the work alongside autonomous tasks on the Kanban / todolist.

Lifecycle:
  - First operator message in a session creates a row with
    `status="in_progress"` and `source_kind="simone_chat"`. The deterministic
    task_id ``simone_chat:<session_id>`` makes the write idempotent across
    reconnects.
  - On `query_complete` with `completed=True`, we set
    `metadata.completion_proposed_at` but do NOT flip status — Simone is
    *proposing* done, not declaring it.
  - The cron `auto_complete_stale` flips proposed rows to `completed` once
    they've been idle past the threshold (default 10 min).
  - An operator can mark complete immediately via the endpoint, or reopen a
    prematurely-completed row.
  - A new operator message after `completed` flips the SAME row back to
    `in_progress` (and clears `completion_proposed_at`).

Status `in_progress` deliberately bypasses dispatch — `claim_next_dispatch_tasks`
filters `WHERE status IN ('open', 'needs_review')` (task_hub.py:1979), so chat
rows never get re-claimed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import sqlite3
from typing import Any, Optional

from universal_agent import task_hub

logger = logging.getLogger(__name__)

SOURCE_KIND = "simone_chat"
PROJECT_KEY = "immediate"
DEFAULT_LABELS = ["simone-chat", task_hub.TASK_LABEL_AGENT_READY]
TASK_ID_PREFIX = "simone_chat:"
TITLE_MAX_LEN = 120


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def task_id_for(session_id: str) -> str:
    """Return the chat-task registry shared across heartbeat sessions."""
    return f"{TASK_ID_PREFIX}{session_id}"


def _truncate_title(text: str) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= TITLE_MAX_LEN:
        return cleaned or "Simone chat session"
    return cleaned[: TITLE_MAX_LEN - 1].rstrip() + "…"


def record_first_operator_message(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    first_message: str,
    source_page: Optional[str] = None,
) -> dict[str, Any]:
    """Create the Task Hub row for a fresh chat session.

    Idempotent: if the row already exists, the existing row is returned with
    its title preserved (we don't overwrite a Simone-refined title with the
    raw first message on a re-call).
    """
    session_id = (session_id or "").strip()
    if not session_id:
        raise ValueError("session_id is required")
    tid = task_id_for(session_id)
    existing = task_hub.get_item(conn, tid)
    if existing:
        return existing
    now = _now_iso()
    item = {
        "task_id": tid,
        "source_kind": SOURCE_KIND,
        "source_ref": session_id,
        "title": _truncate_title(first_message),
        "description": "Interactive Simone chat session.",
        "project_key": PROJECT_KEY,
        "priority": 2,
        "labels": list(DEFAULT_LABELS),
        "status": task_hub.TASK_STATUS_IN_PROGRESS,
        "agent_ready": True,
        "trigger_type": "immediate",
        "metadata": {
            "session_id": session_id,
            "source_page": source_page or "",
            "started_at": now,
            "last_operator_message_at": now,
            "completion_proposed_at": None,
        },
    }
    row = task_hub.upsert_item(conn, item)
    logger.info("simone_chat: created task %s (session=%s)", tid, session_id)
    return row


def on_operator_message(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    text: str,
    source_page: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Handle every inbound operator message.

    - If no row exists yet, creates one (first message of the session).
    - If row exists in a terminal status, flips it back to `in_progress` and
      clears `completion_proposed_at` (resume semantics).
    - Always bumps `last_operator_message_at`.
    """
    session_id = (session_id or "").strip()
    if not session_id:
        return None
    if not (text or "").strip():
        return None
    tid = task_id_for(session_id)
    existing = task_hub.get_item(conn, tid)
    if existing is None:
        return record_first_operator_message(
            conn,
            session_id=session_id,
            first_message=text,
            source_page=source_page,
        )
    now = _now_iso()
    metadata_patch: dict[str, Any] = {"last_operator_message_at": now}
    existing_status = str(existing.get("status") or "").strip().lower()
    update: dict[str, Any] = {
        "task_id": tid,
        "metadata": metadata_patch,
    }
    if existing_status in task_hub.TERMINAL_STATUSES:
        update["status"] = task_hub.TASK_STATUS_IN_PROGRESS
        metadata_patch["completion_proposed_at"] = None
        metadata_patch["resumed_at"] = now
        logger.info("simone_chat: reopened task %s on new operator message", tid)
    return task_hub.upsert_item(conn, update)


def on_query_complete(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    completed: bool,
) -> Optional[dict[str, Any]]:
    """Handle the `query_complete` WebSocket emission.

    When `completed=True`, marks the row as having a completion proposal at
    `now`. Auto-complete cron later promotes proposed → completed once idle.
    Status is NOT changed here — that's deliberate so the operator can keep
    talking and the row stays "In Progress" on the Kanban.
    """
    if not completed:
        return None
    session_id = (session_id or "").strip()
    if not session_id:
        return None
    tid = task_id_for(session_id)
    existing = task_hub.get_item(conn, tid)
    if existing is None:
        return None  # No row yet → no operator message yet → nothing to propose on.
    now = _now_iso()
    update = {
        "task_id": tid,
        "metadata": {"completion_proposed_at": now},
    }
    return task_hub.upsert_item(conn, update)


def mark_complete(
    conn: sqlite3.Connection,
    *,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Promote a chat task to `completed`.

    Caller can specify either `session_id` or `task_id`. No-op if the row
    is already terminal.
    """
    if task_id:
        tid = task_id
    elif session_id:
        tid = task_id_for(session_id)
    else:
        raise ValueError("session_id or task_id is required")
    existing = task_hub.get_item(conn, tid)
    if existing is None:
        return None
    existing_status = str(existing.get("status") or "").strip().lower()
    if existing_status in task_hub.TERMINAL_STATUSES:
        return existing
    now = _now_iso()
    update = {
        "task_id": tid,
        "status": task_hub.TASK_STATUS_COMPLETED,
        "metadata": {"completed_at": now},
    }
    row = task_hub.upsert_item(conn, update)
    logger.info("simone_chat: marked complete %s", tid)
    return row


def reopen(
    conn: sqlite3.Connection,
    *,
    task_id: str,
) -> Optional[dict[str, Any]]:
    """Operator override — flips a `completed` chat task back to `in_progress`.

    Use case: auto-complete fired prematurely but operator wants to resume
    without sending a new message yet (e.g. they need a moment to think).
    Sending a new message would also re-open the row; this endpoint just
    makes the override explicit on the Kanban.
    """
    existing = task_hub.get_item(conn, task_id)
    if existing is None:
        return None
    update = {
        "task_id": task_id,
        "status": task_hub.TASK_STATUS_IN_PROGRESS,
        "metadata": {
            "completion_proposed_at": None,
            "reopened_at": _now_iso(),
        },
    }
    return task_hub.upsert_item(conn, update)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def auto_complete_stale(
    conn: sqlite3.Connection,
    *,
    idle_threshold_minutes: int = 10,
    now: Optional[datetime] = None,
) -> list[str]:
    """Promote proposed rows to `completed` once they've been idle past the threshold.

    Selection criteria:
      - source_kind = 'simone_chat'
      - status = 'in_progress'
      - metadata.completion_proposed_at is set
      - last_operator_message_at <= completion_proposed_at
        (operator didn't reply after Simone proposed — conversation is over)
      - now - completion_proposed_at >= idle_threshold_minutes

    Returns the list of task_ids that were promoted.
    """
    task_hub.ensure_schema(conn)
    threshold = timedelta(minutes=max(1, int(idle_threshold_minutes)))
    cutoff_now = now or datetime.now(timezone.utc)
    rows = conn.execute(
        """
        SELECT task_id, status, metadata_json
        FROM task_hub_items
        WHERE source_kind = ? AND status = ?
        """,
        (SOURCE_KIND, task_hub.TASK_STATUS_IN_PROGRESS),
    ).fetchall()
    promoted: list[str] = []
    for row in rows:
        task_id = str(row["task_id"])
        try:
            import json as _json
            metadata = _json.loads(row["metadata_json"] or "{}") or {}
        except (TypeError, ValueError):
            metadata = {}
        proposed_at = _parse_iso(metadata.get("completion_proposed_at"))
        if proposed_at is None:
            continue
        last_msg_at = _parse_iso(metadata.get("last_operator_message_at"))
        if last_msg_at is not None and last_msg_at > proposed_at:
            continue
        if cutoff_now - proposed_at < threshold:
            continue
        mark_complete(conn, task_id=task_id)
        promoted.append(task_id)
    if promoted:
        logger.info(
            "simone_chat: auto-completed %d stale chat task(s): %s",
            len(promoted),
            promoted,
        )
    return promoted
