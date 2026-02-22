"""Persistent source state helpers."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def get_state(conn: sqlite3.Connection, source_key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT state_json FROM source_state WHERE source_key = ?", (source_key,)).fetchone()
    if row is None:
        return None
    raw = str(row["state_json"])
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def set_state(conn: sqlite3.Connection, source_key: str, state: dict[str, Any]) -> None:
    payload = json.dumps(state, separators=(",", ":"))
    conn.execute(
        """
        INSERT INTO source_state (source_key, state_json, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(source_key) DO UPDATE SET
            state_json = excluded.state_json,
            updated_at = datetime('now')
        """,
        (source_key, payload),
    )
    conn.commit()

