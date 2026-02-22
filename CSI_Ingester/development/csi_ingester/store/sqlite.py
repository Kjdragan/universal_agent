"""SQLite schema and migration helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path


MIGRATION_0001_CORE = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    dedupe_key TEXT NOT NULL,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    emitted_at TEXT,
    subject_json TEXT NOT NULL,
    routing_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    delivered INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_events_dedupe ON events(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);
CREATE INDEX IF NOT EXISTS idx_events_delivered ON events(delivered);

CREATE TABLE IF NOT EXISTS dedupe_keys (
    key TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dedupe_expires ON dedupe_keys(expires_at);

CREATE TABLE IF NOT EXISTS dead_letter (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    error_reason TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

MIGRATION_0002_SOURCE_STATE = """
CREATE TABLE IF NOT EXISTS source_state (
    source_key TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("0001_core", MIGRATION_0001_CORE),
    ("0002_source_state", MIGRATION_0002_SOURCE_STATE),
)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_id TEXT PRIMARY KEY,
            applied_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    applied = {
        str(row["migration_id"])
        for row in conn.execute("SELECT migration_id FROM schema_migrations").fetchall()
    }
    for migration_id, sql in MIGRATIONS:
        if migration_id in applied:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (migration_id, applied_at) VALUES (?, datetime('now'))",
            (migration_id,),
        )
        conn.commit()
