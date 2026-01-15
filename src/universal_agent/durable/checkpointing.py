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
    corpus_data: Optional[str] = None,
) -> str:
    """
    Save a checkpoint with optional corpus data for sub-agent context restoration.
    
    Args:
        conn: Database connection
        run_id: Current run ID
        step_id: Current step ID
        checkpoint_type: Type of checkpoint (e.g., 'research_complete', 'post_replay')
        state_snapshot: Dictionary of state to preserve
        cursor: Optional pagination cursor
        corpus_data: Optional pre-loaded research corpus text for sub-agent injection
    """
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
            cursor_json,
            corpus_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            checkpoint_id,
            run_id,
            step_id,
            _now(),
            checkpoint_type,
            json.dumps(state_snapshot, default=str),
            json.dumps(cursor or {}, default=str),
            corpus_data,
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


def load_corpus_cache(conn: sqlite3.Connection, run_id: str) -> Optional[str]:
    """
    Load the most recent corpus data for a run.
    
    This is used to restore sub-agent context after a restart without
    requiring the agent to re-read all research files.
    
    Args:
        conn: Database connection
        run_id: The run ID to load corpus for
        
    Returns:
        The cached corpus text, or None if no cache exists.
    """
    result = conn.execute(
        """
        SELECT corpus_data FROM checkpoints
        WHERE run_id = ? AND corpus_data IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    return result[0] if result else None
