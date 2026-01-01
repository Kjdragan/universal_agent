import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_checkpoint(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    step_id: str,
    checkpoint_type: str,
    state_snapshot: dict[str, Any],
    cursor: Optional[dict[str, Any]] = None,
) -> str:
    checkpoint_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO checkpoints (
            checkpoint_id,
            run_id,
            step_id,
            created_at,
            checkpoint_type,
            state_snapshot_json,
            cursor_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            checkpoint_id,
            run_id,
            step_id,
            _now(),
            checkpoint_type,
            json.dumps(state_snapshot, default=str),
            json.dumps(cursor or {}, default=str),
        ),
    )
    conn.execute(
        "UPDATE runs SET last_checkpoint_id = ?, updated_at = ? WHERE run_id = ?",
        (checkpoint_id, _now(), run_id),
    )
    conn.commit()
    return checkpoint_id


def load_last_checkpoint(conn: sqlite3.Connection, run_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM checkpoints
        WHERE run_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()
