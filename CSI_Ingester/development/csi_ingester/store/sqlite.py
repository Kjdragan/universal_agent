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

MIGRATION_0003_TOKEN_USAGE = """
CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    process_name TEXT NOT NULL,
    model_name TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_token_usage_occurred_at ON token_usage(occurred_at);
CREATE INDEX IF NOT EXISTS idx_token_usage_process_name ON token_usage(process_name);
CREATE INDEX IF NOT EXISTS idx_token_usage_model_name ON token_usage(model_name);
"""

MIGRATION_0004_RSS_ANALYSIS = """
CREATE TABLE IF NOT EXISTS rss_event_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    event_db_id INTEGER,
    source TEXT NOT NULL DEFAULT 'youtube_channel_rss',
    video_id TEXT,
    channel_id TEXT,
    channel_name TEXT,
    title TEXT,
    published_at TEXT,
    transcript_status TEXT NOT NULL DEFAULT 'missing',
    transcript_chars INTEGER DEFAULT 0,
    transcript_ref TEXT,
    category TEXT NOT NULL DEFAULT 'other_interest',
    summary_text TEXT,
    model_name TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    analysis_json TEXT NOT NULL DEFAULT '{}',
    analyzed_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_rss_event_analysis_analyzed_at ON rss_event_analysis(analyzed_at);
CREATE INDEX IF NOT EXISTS idx_rss_event_analysis_category ON rss_event_analysis(category);
CREATE INDEX IF NOT EXISTS idx_rss_event_analysis_channel_id ON rss_event_analysis(channel_id);

CREATE TABLE IF NOT EXISTS trend_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_key TEXT UNIQUE NOT NULL,
    window_start_utc TEXT NOT NULL,
    window_end_utc TEXT NOT NULL,
    model_name TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    report_markdown TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trend_reports_created_at ON trend_reports(created_at);
"""

MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("0001_core", MIGRATION_0001_CORE),
    ("0002_source_state", MIGRATION_0002_SOURCE_STATE),
    ("0003_token_usage", MIGRATION_0003_TOKEN_USAGE),
    ("0004_rss_analysis", MIGRATION_0004_RSS_ANALYSIS),
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
