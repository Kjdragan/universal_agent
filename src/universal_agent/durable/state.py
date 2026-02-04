import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_run(
    conn: sqlite3.Connection,
    run_id: str,
    entrypoint: str,
    run_spec: dict[str, Any],
    run_mode: Optional[str] = None,
    job_path: Optional[str] = None,
    last_job_prompt: Optional[str] = None,
    parent_run_id: Optional[str] = None,
    iteration_count: int = 0,
    max_iterations: Optional[int] = None,
    completion_promise: Optional[str] = None,
    total_tokens: int = 0,
    status: str = "running",
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT OR IGNORE INTO runs (
            run_id, created_at, updated_at, status, entrypoint, run_spec_json,
            run_mode, job_path, last_job_prompt, parent_run_id,
            iteration_count, max_iterations, completion_promise, total_tokens
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            now,
            now,
            status,
            entrypoint,
            json.dumps(run_spec, default=str),
            run_mode,
            job_path,
            last_job_prompt,
            parent_run_id,
            iteration_count,
            max_iterations,
            completion_promise,
            total_tokens,
        ),
    )
    conn.execute(
        """
        UPDATE runs
        SET updated_at = ?,
            status = ?,
            entrypoint = ?,
            run_spec_json = ?,
            run_mode = ?,
            job_path = ?,
            last_job_prompt = ?,
            parent_run_id = ?,
            max_iterations = COALESCE(?, max_iterations),
            completion_promise = COALESCE(?, completion_promise),
            total_tokens = ?
        WHERE run_id = ?
        """,
        (
            now,
            status,
            entrypoint,
            json.dumps(run_spec, default=str),
            run_mode,
            job_path,
            last_job_prompt,
            parent_run_id,
            max_iterations,
            completion_promise,
            total_tokens,
            run_id,
        ),
    )
    conn.commit()


def update_run_status(conn: sqlite3.Connection, run_id: str, status: str) -> None:
    conn.execute(
        "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
        (status, _now(), run_id),
    )
    conn.commit()


def update_run_provider_session(
    conn: sqlite3.Connection,
    run_id: str,
    session_id: Optional[str],
    forked_from: Optional[str] = None,
) -> None:
    conn.execute(
        """
        UPDATE runs
        SET provider_session_id = ?,
            provider_session_forked_from = COALESCE(?, provider_session_forked_from),
            provider_session_last_seen_at = ?,
            updated_at = ?
        WHERE run_id = ?
        """,
        (session_id, forked_from, _now(), _now(), run_id),
    )
    conn.commit()


def update_run_tokens(conn: sqlite3.Connection, run_id: str, total_tokens: int) -> None:
    conn.execute(
        "UPDATE runs SET total_tokens = ?, updated_at = ? WHERE run_id = ?",
        (total_tokens, _now(), run_id),
    )
    conn.commit()


def start_step(
    conn: sqlite3.Connection,
    run_id: str,
    step_id: str,
    step_index: int,
    phase: str = "unspecified",
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO run_steps (
            step_id, run_id, step_index, created_at, updated_at, status, phase
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (step_id, run_id, step_index, now, now, "running", phase),
    )
    conn.execute(
        "UPDATE runs SET current_step_id = ?, updated_at = ? WHERE run_id = ?",
        (step_id, now, run_id),
    )
    conn.commit()


def complete_step(
    conn: sqlite3.Connection,
    step_id: str,
    status: str,
    error_code: Optional[str] = None,
    error_detail: Optional[str] = None,
) -> None:
    conn.execute(
        """
        UPDATE run_steps
        SET status = ?, updated_at = ?, error_code = ?, error_detail = ?
        WHERE step_id = ?
        """,
        (status, _now(), error_code, error_detail, step_id),
    )
    conn.commit()


def update_step_phase(conn: sqlite3.Connection, step_id: str, phase: str) -> None:
    conn.execute(
        "UPDATE run_steps SET phase = ?, updated_at = ? WHERE step_id = ?",
        (phase, _now(), step_id),
    )
    conn.commit()


def get_run(conn: sqlite3.Connection, run_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()


def get_run_status(conn: sqlite3.Connection, run_id: str) -> Optional[str]:
    row = conn.execute(
        "SELECT status FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return row["status"] if row else None


def is_cancel_requested(conn: sqlite3.Connection, run_id: str) -> bool:
    return get_run_status(conn, run_id) == "cancel_requested"


def request_run_cancel(
    conn: sqlite3.Connection, run_id: str, reason: Optional[str] = None
) -> None:
    conn.execute(
        """
        UPDATE runs
        SET status = ?, cancel_requested_at = ?, cancel_reason = ?, updated_at = ?
        WHERE run_id = ?
        """,
        ("cancel_requested", _now(), reason, _now(), run_id),
    )
    conn.commit()


def mark_run_cancelled(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute(
        "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
        ("cancelled", _now(), run_id),
    )
    conn.commit()


def list_runs_with_status(
    conn: sqlite3.Connection,
    statuses: Iterable[str],
    limit: int = 25,
) -> list[sqlite3.Row]:
    status_list = [status for status in statuses if status]
    if not status_list:
        return []
    placeholders = ", ".join("?" for _ in status_list)
    rows = conn.execute(
        f"""
        SELECT run_id, status, lease_owner, lease_expires_at, created_at
        FROM runs
        WHERE status IN ({placeholders})
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (*status_list, limit),
    ).fetchall()
    return list(rows)


def acquire_run_lease(
    conn: sqlite3.Connection,
    run_id: str,
    lease_owner: str,
    lease_ttl_seconds: int,
) -> bool:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=lease_ttl_seconds)
    now_iso = now.isoformat()
    expires_iso = expires_at.isoformat()
    result = conn.execute(
        """
        UPDATE runs
        SET lease_owner = ?,
            lease_expires_at = ?,
            last_heartbeat_at = ?,
            updated_at = ?,
            status = CASE WHEN status = 'queued' THEN 'running' ELSE status END
        WHERE run_id = ?
          AND status IN ('queued', 'running')
          AND (lease_expires_at IS NULL OR lease_expires_at < ?)
        """,
        (lease_owner, expires_iso, now_iso, now_iso, run_id, now_iso),
    )
    conn.commit()
    return result.rowcount == 1


def heartbeat_run_lease(
    conn: sqlite3.Connection,
    run_id: str,
    lease_owner: str,
    lease_ttl_seconds: int,
) -> bool:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=lease_ttl_seconds)
    now_iso = now.isoformat()
    expires_iso = expires_at.isoformat()
    result = conn.execute(
        """
        UPDATE runs
        SET lease_expires_at = ?, last_heartbeat_at = ?, updated_at = ?
        WHERE run_id = ? AND lease_owner = ?
        """,
        (expires_iso, now_iso, now_iso, run_id, lease_owner),
    )
    conn.commit()
    return result.rowcount == 1


def release_run_lease(
    conn: sqlite3.Connection, run_id: str, lease_owner: str
) -> None:
    conn.execute(
        """
        UPDATE runs
        SET lease_owner = NULL,
            lease_expires_at = NULL,
            updated_at = ?
        WHERE run_id = ? AND lease_owner = ?
        """,
        (_now(), run_id, lease_owner),
    )
    conn.commit()


def get_step_count(conn: sqlite3.Connection, run_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(1) AS count FROM run_steps WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return int(row["count"]) if row else 0


def increment_iteration_count(conn: sqlite3.Connection, run_id: str) -> int:
    conn.execute(
        "UPDATE runs SET iteration_count = iteration_count + 1, updated_at = ? WHERE run_id = ?",
        (_now(), run_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT iteration_count FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return int(row["iteration_count"]) if row else 0


def get_iteration_info(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT iteration_count, max_iterations, completion_promise, run_spec_json FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return dict(row) if row else {"iteration_count": 0, "max_iterations": None, "completion_promise": None, "run_spec_json": "{}"}
