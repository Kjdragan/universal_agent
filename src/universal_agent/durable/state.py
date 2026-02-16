import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_or_none(value: Optional[dict[str, Any]]) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, default=str)


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


def upsert_vp_session(
    conn: sqlite3.Connection,
    vp_id: str,
    runtime_id: str,
    status: str,
    session_id: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    lease_owner: Optional[str] = None,
    lease_expires_at: Optional[str] = None,
    last_heartbeat_at: Optional[str] = None,
    last_error: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    now = _now()
    metadata_json = _json_or_none(metadata)
    conn.execute(
        """
        INSERT OR IGNORE INTO vp_sessions (
            vp_id,
            runtime_id,
            session_id,
            workspace_dir,
            status,
            lease_owner,
            lease_expires_at,
            last_heartbeat_at,
            last_error,
            metadata_json,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            vp_id,
            runtime_id,
            session_id,
            workspace_dir,
            status,
            lease_owner,
            lease_expires_at,
            last_heartbeat_at,
            last_error,
            metadata_json,
            now,
            now,
        ),
    )
    conn.execute(
        """
        UPDATE vp_sessions
        SET runtime_id = ?,
            session_id = COALESCE(?, session_id),
            workspace_dir = COALESCE(?, workspace_dir),
            status = ?,
            lease_owner = COALESCE(?, lease_owner),
            lease_expires_at = COALESCE(?, lease_expires_at),
            last_heartbeat_at = COALESCE(?, last_heartbeat_at),
            last_error = COALESCE(?, last_error),
            metadata_json = COALESCE(?, metadata_json),
            updated_at = ?
        WHERE vp_id = ?
        """,
        (
            runtime_id,
            session_id,
            workspace_dir,
            status,
            lease_owner,
            lease_expires_at,
            last_heartbeat_at,
            last_error,
            metadata_json,
            now,
            vp_id,
        ),
    )
    conn.commit()


def get_vp_session(conn: sqlite3.Connection, vp_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM vp_sessions WHERE vp_id = ?",
        (vp_id,),
    ).fetchone()


def list_vp_sessions(
    conn: sqlite3.Connection,
    statuses: Optional[Iterable[str]] = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    if statuses:
        status_list = [s for s in statuses if s]
        if not status_list:
            return []
        placeholders = ", ".join("?" for _ in status_list)
        rows = conn.execute(
            f"""
            SELECT *
            FROM vp_sessions
            WHERE status IN ({placeholders})
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (*status_list, limit),
        ).fetchall()
        return list(rows)

    rows = conn.execute(
        """
        SELECT *
        FROM vp_sessions
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return list(rows)


def update_vp_session_status(
    conn: sqlite3.Connection,
    vp_id: str,
    status: str,
    last_error: Optional[str] = None,
) -> None:
    conn.execute(
        """
        UPDATE vp_sessions
        SET status = ?,
            last_error = COALESCE(?, last_error),
            updated_at = ?
        WHERE vp_id = ?
        """,
        (status, last_error, _now(), vp_id),
    )
    conn.commit()


def acquire_vp_session_lease(
    conn: sqlite3.Connection,
    vp_id: str,
    lease_owner: str,
    lease_ttl_seconds: int,
) -> bool:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    expires_iso = (now + timedelta(seconds=lease_ttl_seconds)).isoformat()
    result = conn.execute(
        """
        UPDATE vp_sessions
        SET lease_owner = ?,
            lease_expires_at = ?,
            last_heartbeat_at = ?,
            updated_at = ?,
            status = CASE
                WHEN status IN ('idle', 'paused', 'degraded', 'recovering') THEN 'active'
                ELSE status
            END
        WHERE vp_id = ?
          AND status IN ('idle', 'active', 'paused', 'degraded', 'recovering')
          AND (
            lease_owner = ?
            OR lease_expires_at IS NULL
            OR lease_expires_at < ?
          )
        """,
        (
            lease_owner,
            expires_iso,
            now_iso,
            now_iso,
            vp_id,
            lease_owner,
            now_iso,
        ),
    )
    conn.commit()
    return result.rowcount == 1


def heartbeat_vp_session_lease(
    conn: sqlite3.Connection,
    vp_id: str,
    lease_owner: str,
    lease_ttl_seconds: int,
) -> bool:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    expires_iso = (now + timedelta(seconds=lease_ttl_seconds)).isoformat()
    result = conn.execute(
        """
        UPDATE vp_sessions
        SET lease_expires_at = ?,
            last_heartbeat_at = ?,
            updated_at = ?
        WHERE vp_id = ? AND lease_owner = ?
        """,
        (expires_iso, now_iso, now_iso, vp_id, lease_owner),
    )
    conn.commit()
    return result.rowcount == 1


def release_vp_session_lease(
    conn: sqlite3.Connection,
    vp_id: str,
    lease_owner: str,
) -> None:
    conn.execute(
        """
        UPDATE vp_sessions
        SET lease_owner = NULL,
            lease_expires_at = NULL,
            updated_at = ?
        WHERE vp_id = ? AND lease_owner = ?
        """,
        (_now(), vp_id, lease_owner),
    )
    conn.commit()


def append_vp_session_event(
    conn: sqlite3.Connection,
    event_id: str,
    vp_id: str,
    event_type: str,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO vp_session_events (
            event_id,
            vp_id,
            event_type,
            payload_json,
            created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (event_id, vp_id, event_type, _json_or_none(payload), _now()),
    )
    conn.commit()


def list_vp_session_events(
    conn: sqlite3.Connection,
    vp_id: Optional[str] = None,
    event_types: Optional[Iterable[str]] = None,
    limit: int = 250,
) -> list[sqlite3.Row]:
    params: list[Any] = []
    where_clauses: list[str] = []

    if vp_id:
        where_clauses.append("vp_id = ?")
        params.append(vp_id)

    if event_types:
        event_type_values = [event_type for event_type in event_types if event_type]
        if event_type_values:
            placeholders = ", ".join("?" for _ in event_type_values)
            where_clauses.append(f"event_type IN ({placeholders})")
            params.extend(event_type_values)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    params.append(limit)

    rows = conn.execute(
        f"""
        SELECT *
        FROM vp_session_events
        {where_sql}
        ORDER BY created_at ASC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return list(rows)


def upsert_vp_mission(
    conn: sqlite3.Connection,
    mission_id: str,
    vp_id: str,
    status: str,
    objective: str,
    budget: Optional[dict[str, Any]] = None,
    result_ref: Optional[str] = None,
    run_id: Optional[str] = None,
    started_at: Optional[str] = None,
    completed_at: Optional[str] = None,
) -> None:
    now = _now()
    budget_json = _json_or_none(budget)
    conn.execute(
        """
        INSERT OR IGNORE INTO vp_missions (
            mission_id,
            vp_id,
            run_id,
            status,
            objective,
            budget_json,
            result_ref,
            created_at,
            started_at,
            completed_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            vp_id,
            run_id,
            status,
            objective,
            budget_json,
            result_ref,
            now,
            started_at,
            completed_at,
            now,
        ),
    )
    conn.execute(
        """
        UPDATE vp_missions
        SET vp_id = ?,
            run_id = COALESCE(?, run_id),
            status = ?,
            objective = ?,
            budget_json = COALESCE(?, budget_json),
            result_ref = COALESCE(?, result_ref),
            started_at = COALESCE(?, started_at),
            completed_at = COALESCE(?, completed_at),
            updated_at = ?
        WHERE mission_id = ?
        """,
        (
            vp_id,
            run_id,
            status,
            objective,
            budget_json,
            result_ref,
            started_at,
            completed_at,
            now,
            mission_id,
        ),
    )
    conn.commit()


def get_vp_mission(conn: sqlite3.Connection, mission_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM vp_missions WHERE mission_id = ?",
        (mission_id,),
    ).fetchone()


def list_vp_missions(
    conn: sqlite3.Connection,
    vp_id: str,
    statuses: Optional[Iterable[str]] = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    if statuses:
        status_list = [s for s in statuses if s]
        if not status_list:
            return []
        placeholders = ", ".join("?" for _ in status_list)
        rows = conn.execute(
            f"""
            SELECT *
            FROM vp_missions
            WHERE vp_id = ?
              AND status IN ({placeholders})
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (vp_id, *status_list, limit),
        ).fetchall()
        return list(rows)

    rows = conn.execute(
        """
        SELECT *
        FROM vp_missions
        WHERE vp_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (vp_id, limit),
    ).fetchall()
    return list(rows)


def append_vp_event(
    conn: sqlite3.Connection,
    event_id: str,
    mission_id: str,
    vp_id: str,
    event_type: str,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO vp_events (
            event_id,
            mission_id,
            vp_id,
            event_type,
            payload_json,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (event_id, mission_id, vp_id, event_type, _json_or_none(payload), _now()),
    )
    conn.commit()


def list_vp_events(
    conn: sqlite3.Connection,
    mission_id: Optional[str] = None,
    vp_id: Optional[str] = None,
    limit: int = 250,
) -> list[sqlite3.Row]:
    if mission_id and vp_id:
        rows = conn.execute(
            """
            SELECT *
            FROM vp_events
            WHERE mission_id = ? AND vp_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (mission_id, vp_id, limit),
        ).fetchall()
        return list(rows)

    if mission_id:
        rows = conn.execute(
            """
            SELECT *
            FROM vp_events
            WHERE mission_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (mission_id, limit),
        ).fetchall()
        return list(rows)

    if vp_id:
        rows = conn.execute(
            """
            SELECT *
            FROM vp_events
            WHERE vp_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (vp_id, limit),
        ).fetchall()
        return list(rows)

    rows = conn.execute(
        """
        SELECT *
        FROM vp_events
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return list(rows)
