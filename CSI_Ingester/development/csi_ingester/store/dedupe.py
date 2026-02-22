"""Dedupe helpers."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


def has_key(conn: sqlite3.Connection, key: str) -> bool:
    row = conn.execute(
        "SELECT key FROM dedupe_keys WHERE key = ? AND expires_at > datetime('now') LIMIT 1",
        (key,),
    ).fetchone()
    return row is not None


def upsert_key(conn: sqlite3.Connection, key: str, ttl_days: int = 90) -> None:
    expires_at = (datetime.now(timezone.utc) + timedelta(days=max(1, ttl_days))).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO dedupe_keys(key, expires_at) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET expires_at = excluded.expires_at
        """,
        (key, expires_at),
    )
    conn.commit()

