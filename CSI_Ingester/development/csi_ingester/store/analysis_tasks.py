"""Persistent analysis task queue helpers."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

TASK_STATUS_PENDING = "pending"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CANCELED = "canceled"

TERMINAL_TASK_STATUSES = {
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_CANCELED,
}


def _json_load(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        parsed = json.loads(raw)
    except Exception:
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "task_id": str(row["task_id"]),
        "request_source": str(row["request_source"] or "ua"),
        "request_type": str(row["request_type"] or ""),
        "priority": int(row["priority"] or 0),
        "status": str(row["status"] or TASK_STATUS_PENDING),
        "payload": _json_load(str(row["payload_json"] or "{}"), {}),
        "result": _json_load(str(row["result_json"] or "{}"), {}),
        "error_text": str(row["error_text"] or ""),
        "attempts": int(row["attempts"] or 0),
        "claim_token": str(row["claim_token"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "started_at": str(row["started_at"] or ""),
        "completed_at": str(row["completed_at"] or ""),
    }


def get_task(conn: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM analysis_tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def list_tasks(
    conn: sqlite3.Connection,
    *,
    status: str = "",
    request_type: str = "",
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if status.strip():
        where.append("status = ?")
        params.append(status.strip())
    if request_type.strip():
        where.append("request_type = ?")
        params.append(request_type.strip())
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.extend([max(1, int(limit)), max(0, int(offset))])
    rows = conn.execute(
        f"""
        SELECT *
        FROM analysis_tasks
        {where_sql}
        ORDER BY
            CASE status
                WHEN 'running' THEN 0
                WHEN 'pending' THEN 1
                WHEN 'failed' THEN 2
                WHEN 'completed' THEN 3
                WHEN 'canceled' THEN 4
                ELSE 5
            END,
            priority DESC,
            id DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def create_task(
    conn: sqlite3.Connection,
    *,
    request_type: str,
    payload: dict[str, Any] | None = None,
    priority: int = 50,
    request_source: str = "ua",
    task_id: str = "",
) -> dict[str, Any]:
    normalized_type = request_type.strip()
    if not normalized_type:
        raise ValueError("request_type must be non-empty")
    normalized_source = request_source.strip() or "ua"
    normalized_task_id = task_id.strip() or f"analysis_{uuid.uuid4().hex}"
    normalized_payload = payload if isinstance(payload, dict) else {}
    conn.execute(
        """
        INSERT INTO analysis_tasks (
            task_id, request_source, request_type, priority, status, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            normalized_task_id,
            normalized_source,
            normalized_type,
            int(priority),
            TASK_STATUS_PENDING,
            json.dumps(normalized_payload, separators=(",", ":"), sort_keys=True),
        ),
    )
    conn.commit()
    created = get_task(conn, normalized_task_id)
    if created is None:
        raise RuntimeError("task not found after insert")
    return created


def claim_next_task(
    conn: sqlite3.Connection,
    *,
    claim_token: str,
    request_types: list[str] | None = None,
    max_attempts: int = 3,
) -> dict[str, Any] | None:
    types = [item.strip() for item in (request_types or []) if item.strip()]
    params: list[Any] = [max(1, int(max_attempts))]
    where = "status = ? AND attempts < ?"
    params.insert(0, TASK_STATUS_PENDING)
    if types:
        placeholders = ",".join(["?"] * len(types))
        where += f" AND request_type IN ({placeholders})"
        params.extend(types)
    row = conn.execute(
        f"""
        SELECT task_id
        FROM analysis_tasks
        WHERE {where}
        ORDER BY priority DESC, id ASC
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    if row is None:
        return None
    task_id = str(row["task_id"])
    updated = conn.execute(
        """
        UPDATE analysis_tasks
        SET
            status = ?,
            claim_token = ?,
            attempts = attempts + 1,
            started_at = COALESCE(started_at, datetime('now')),
            updated_at = datetime('now')
        WHERE task_id = ? AND status = ?
        """,
        (
            TASK_STATUS_RUNNING,
            claim_token,
            task_id,
            TASK_STATUS_PENDING,
        ),
    )
    conn.commit()
    if int(updated.rowcount or 0) == 0:
        return None
    return get_task(conn, task_id)


def complete_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    claim_token: str,
    result: dict[str, Any] | None = None,
) -> bool:
    payload = result if isinstance(result, dict) else {}
    updated = conn.execute(
        """
        UPDATE analysis_tasks
        SET
            status = ?,
            result_json = ?,
            error_text = '',
            updated_at = datetime('now'),
            completed_at = datetime('now')
        WHERE task_id = ? AND status = ? AND claim_token = ?
        """,
        (
            TASK_STATUS_COMPLETED,
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            task_id,
            TASK_STATUS_RUNNING,
            claim_token,
        ),
    )
    conn.commit()
    return int(updated.rowcount or 0) > 0


def fail_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    claim_token: str,
    error_text: str,
    retry: bool = False,
) -> bool:
    next_status = TASK_STATUS_PENDING if retry else TASK_STATUS_FAILED
    completed_at_expr = "NULL" if retry else "datetime('now')"
    updated = conn.execute(
        f"""
        UPDATE analysis_tasks
        SET
            status = ?,
            error_text = ?,
            claim_token = CASE WHEN ? THEN '' ELSE claim_token END,
            updated_at = datetime('now'),
            completed_at = {completed_at_expr}
        WHERE task_id = ? AND status = ? AND claim_token = ?
        """,
        (
            next_status,
            (error_text or "")[:4000],
            1 if retry else 0,
            task_id,
            TASK_STATUS_RUNNING,
            claim_token,
        ),
    )
    conn.commit()
    return int(updated.rowcount or 0) > 0


def cancel_task(conn: sqlite3.Connection, *, task_id: str, reason: str = "") -> dict[str, Any] | None:
    task = get_task(conn, task_id)
    if task is None:
        return None
    if task["status"] in TERMINAL_TASK_STATUSES:
        return task
    conn.execute(
        """
        UPDATE analysis_tasks
        SET
            status = ?,
            error_text = ?,
            updated_at = datetime('now'),
            completed_at = datetime('now')
        WHERE task_id = ? AND status IN (?, ?)
        """,
        (
            TASK_STATUS_CANCELED,
            (reason or "canceled").strip()[:4000],
            task_id,
            TASK_STATUS_PENDING,
            TASK_STATUS_RUNNING,
        ),
    )
    conn.commit()
    return get_task(conn, task_id)
