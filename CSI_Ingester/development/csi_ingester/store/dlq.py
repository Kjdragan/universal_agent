"""Dead-letter queue persistence."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def enqueue(conn: sqlite3.Connection, *, event_id: str, event: dict[str, Any], error_reason: str, retry_count: int) -> None:
    conn.execute(
        """
        INSERT INTO dead_letter(event_id, event_json, error_reason, retry_count)
        VALUES (?, ?, ?, ?)
        """,
        (
            event_id,
            json.dumps(event, separators=(",", ":"), sort_keys=True),
            error_reason,
            max(0, int(retry_count)),
        ),
    )
    conn.commit()


def list_entries(
    conn: sqlite3.Connection,
    *,
    event_id: str = "",
    limit: int = 100,
) -> list[sqlite3.Row]:
    max_rows = max(1, min(int(limit), 1000))
    if event_id:
        return list(
            conn.execute(
                """
                SELECT id, event_id, event_json, error_reason, retry_count, created_at
                FROM dead_letter
                WHERE event_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (event_id, max_rows),
            ).fetchall()
        )
    return list(
        conn.execute(
            """
            SELECT id, event_id, event_json, error_reason, retry_count, created_at
            FROM dead_letter
            ORDER BY id ASC
            LIMIT ?
            """,
            (max_rows,),
        ).fetchall()
    )


def delete_entry(conn: sqlite3.Connection, row_id: int) -> None:
    conn.execute("DELETE FROM dead_letter WHERE id = ?", (int(row_id),))
    conn.commit()
