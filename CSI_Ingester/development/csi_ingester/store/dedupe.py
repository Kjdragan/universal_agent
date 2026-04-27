"""Dedupe helpers.

Manages the dedupe_keys table in csi.db to prevent processing the
same content item twice.  Each key has a TTL (expires_at); expired
keys are functionally invisible to ``has_key`` and are periodically
purged by ``purge_expired`` to keep the table lean.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import sqlite3

logger = logging.getLogger(__name__)

# ── Source-specific TTL defaults ────────────────────────────────────
# Reddit posts go cold within days; 30 days is more than sufficient.
# YouTube videos can resurface in playlists months later; keep 90 days.
# Threads are ephemeral; 30 days is plenty.
DEFAULT_TTL_DAYS = 90
SOURCE_TTL_DAYS: dict[str, int] = {
    "reddit": 30,
    "threads": 30,
    "youtube": 90,
}


def ttl_for_key(key: str) -> int:
    """Return the appropriate TTL in days based on the dedupe key prefix."""
    for prefix, ttl in SOURCE_TTL_DAYS.items():
        if key.startswith(f"{prefix}:"):
            return ttl
    return DEFAULT_TTL_DAYS


def has_key(conn: sqlite3.Connection, key: str) -> bool:
    row = conn.execute(
        "SELECT key FROM dedupe_keys WHERE key = ? AND expires_at > datetime('now') LIMIT 1",
        (key,),
    ).fetchone()
    return row is not None


def upsert_key(conn: sqlite3.Connection, key: str, ttl_days: int | None = None) -> None:
    if ttl_days is None:
        ttl_days = ttl_for_key(key)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=max(1, ttl_days))).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO dedupe_keys(key, expires_at) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET expires_at = excluded.expires_at
        """,
        (key, expires_at),
    )
    conn.commit()


def purge_expired(conn: sqlite3.Connection) -> int:
    """Delete expired dedupe keys and return the count of deleted rows.

    This should be called periodically (e.g. once per hour) to prevent
    unbounded table growth.  The idx_dedupe_expires index makes the
    DELETE efficient.
    """
    cursor = conn.execute(
        "DELETE FROM dedupe_keys WHERE expires_at <= datetime('now')"
    )
    deleted = cursor.rowcount
    if deleted > 0:
        conn.commit()
        logger.info("Purged %d expired dedupe keys", deleted)
    return deleted


def count_keys(conn: sqlite3.Connection) -> int:
    """Return the total number of dedupe keys (expired + active)."""
    row = conn.execute("SELECT COUNT(*) AS cnt FROM dedupe_keys").fetchone()
    return int(row[0]) if row else 0
