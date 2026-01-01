import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_run(
    conn: sqlite3.Connection,
    run_id: str,
    entrypoint: str,
    run_spec: dict[str, Any],
    status: str = "running",
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT OR IGNORE INTO runs (
            run_id, created_at, updated_at, status, entrypoint, run_spec_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, now, now, status, entrypoint, json.dumps(run_spec, default=str)),
    )
    conn.execute(
        """
        UPDATE runs
        SET updated_at = ?, status = ?, entrypoint = ?, run_spec_json = ?
        WHERE run_id = ?
        """,
        (now, status, entrypoint, json.dumps(run_spec, default=str), run_id),
    )
    conn.commit()


def update_run_status(conn: sqlite3.Connection, run_id: str, status: str) -> None:
    conn.execute(
        "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
        (status, _now(), run_id),
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


def get_run(conn: sqlite3.Connection, run_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()


def get_step_count(conn: sqlite3.Connection, run_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(1) AS count FROM run_steps WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return int(row["count"]) if row else 0
