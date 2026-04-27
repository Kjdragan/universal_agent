"""SQLite schema and migration helpers."""

from __future__ import annotations

from pathlib import Path
import sqlite3

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

MIGRATION_0005_ANALYST = """
CREATE TABLE IF NOT EXISTS analysis_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL,
    request_source TEXT NOT NULL DEFAULT 'ua',
    request_type TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'pending',
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error_text TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    claim_token TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_analysis_tasks_status_priority ON analysis_tasks(status, priority DESC, id ASC);
CREATE INDEX IF NOT EXISTS idx_analysis_tasks_request_type ON analysis_tasks(request_type);
CREATE INDEX IF NOT EXISTS idx_analysis_tasks_created_at ON analysis_tasks(created_at);

CREATE TABLE IF NOT EXISTS insight_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_key TEXT UNIQUE NOT NULL,
    report_type TEXT NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_insight_reports_created_at ON insight_reports(created_at);
CREATE INDEX IF NOT EXISTS idx_insight_reports_type ON insight_reports(report_type);

CREATE TABLE IF NOT EXISTS category_quality_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    total_items INTEGER NOT NULL DEFAULT 0,
    other_interest_items INTEGER NOT NULL DEFAULT 0,
    other_interest_ratio REAL NOT NULL DEFAULT 0.0,
    dynamic_categories INTEGER NOT NULL DEFAULT 0,
    uncategorized_items INTEGER NOT NULL DEFAULT 0,
    new_category_min_topic_hits INTEGER NOT NULL DEFAULT 8,
    action TEXT,
    notes_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_category_quality_snapshots_observed_at ON category_quality_snapshots(observed_at);
"""

MIGRATION_0006_DELIVERY_ATTEMPTS = """
CREATE TABLE IF NOT EXISTS delivery_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT 'ua_signals_ingest',
    delivered INTEGER NOT NULL DEFAULT 0,
    status_code INTEGER NOT NULL DEFAULT 0,
    error_class TEXT,
    error_detail TEXT,
    attempted_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_delivery_attempts_event_id ON delivery_attempts(event_id);
CREATE INDEX IF NOT EXISTS idx_delivery_attempts_target ON delivery_attempts(target, attempted_at DESC);
CREATE INDEX IF NOT EXISTS idx_delivery_attempts_status ON delivery_attempts(delivered, status_code, attempted_at DESC);
"""

MIGRATION_0007_OPPORTUNITY_BUNDLES = """
CREATE TABLE IF NOT EXISTS opportunity_bundles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id TEXT UNIQUE NOT NULL,
    report_key TEXT,
    window_start_utc TEXT NOT NULL,
    window_end_utc TEXT NOT NULL,
    confidence_method TEXT NOT NULL DEFAULT 'heuristic',
    quality_summary_json TEXT NOT NULL DEFAULT '{}',
    opportunities_json TEXT NOT NULL DEFAULT '[]',
    artifact_markdown_path TEXT,
    artifact_json_path TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_opportunity_bundles_created_at ON opportunity_bundles(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_opportunity_bundles_window ON opportunity_bundles(window_end_utc DESC);
"""

MIGRATION_0008_CROSS_SOURCE_ANALYSIS = """
CREATE TABLE IF NOT EXISTS reddit_event_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    event_db_id INTEGER,
    source TEXT NOT NULL DEFAULT 'reddit_discovery',
    post_id TEXT,
    subreddit TEXT,
    title TEXT,
    url TEXT,
    author TEXT,
    score INTEGER DEFAULT 0,
    num_comments INTEGER DEFAULT 0,
    occurred_at TEXT,
    category TEXT NOT NULL DEFAULT 'other_interest',
    summary_text TEXT,
    model_name TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    analysis_json TEXT NOT NULL DEFAULT '{}',
    analyzed_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reddit_event_analysis_analyzed_at ON reddit_event_analysis(analyzed_at);
CREATE INDEX IF NOT EXISTS idx_reddit_event_analysis_category ON reddit_event_analysis(category);
CREATE INDEX IF NOT EXISTS idx_reddit_event_analysis_subreddit ON reddit_event_analysis(subreddit);

CREATE TABLE IF NOT EXISTS threads_event_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    event_db_id INTEGER,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    media_id TEXT,
    trend_bucket TEXT,
    query_term TEXT,
    username TEXT,
    text TEXT,
    permalink TEXT,
    timestamp TEXT,
    like_count INTEGER DEFAULT 0,
    reply_count INTEGER DEFAULT 0,
    repost_count INTEGER DEFAULT 0,
    quote_count INTEGER DEFAULT 0,
    category TEXT NOT NULL DEFAULT 'other_interest',
    summary_text TEXT,
    model_name TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    analysis_json TEXT NOT NULL DEFAULT '{}',
    analyzed_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_threads_event_analysis_analyzed_at ON threads_event_analysis(analyzed_at);
CREATE INDEX IF NOT EXISTS idx_threads_event_analysis_source ON threads_event_analysis(source, analyzed_at DESC);
CREATE INDEX IF NOT EXISTS idx_threads_event_analysis_bucket ON threads_event_analysis(trend_bucket, analyzed_at DESC);

CREATE TABLE IF NOT EXISTS global_trend_briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_key TEXT UNIQUE NOT NULL,
    window_start_utc TEXT NOT NULL,
    window_end_utc TEXT NOT NULL,
    model_name TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    brief_markdown TEXT NOT NULL,
    brief_json TEXT NOT NULL,
    artifact_markdown_path TEXT,
    artifact_json_path TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_global_trend_briefs_created_at ON global_trend_briefs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_global_trend_briefs_window ON global_trend_briefs(window_end_utc DESC);
"""

MIGRATION_0009_SOURCE_MANAGEMENT = """
CREATE TABLE IF NOT EXISTS youtube_channels (
    channel_id     TEXT PRIMARY KEY,
    channel_name   TEXT NOT NULL,
    rss_feed_url   TEXT,
    youtube_url    TEXT,
    domain         TEXT NOT NULL DEFAULT 'other_signal',
    tier           INTEGER NOT NULL DEFAULT 2,
    quality_score  REAL NOT NULL DEFAULT 0.5,
    items_assessed INTEGER NOT NULL DEFAULT 0,
    active         INTEGER NOT NULL DEFAULT 1,
    seed_video_count INTEGER DEFAULT 0,
    added_at       TEXT DEFAULT (datetime('now')),
    last_assessed  TEXT,
    demoted_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_youtube_channels_domain ON youtube_channels(domain);
CREATE INDEX IF NOT EXISTS idx_youtube_channels_tier ON youtube_channels(tier);
CREATE INDEX IF NOT EXISTS idx_youtube_channels_active ON youtube_channels(active);

CREATE TABLE IF NOT EXISTS reddit_sources (
    subreddit      TEXT PRIMARY KEY,
    domain         TEXT NOT NULL DEFAULT 'other_signal',
    tier           INTEGER NOT NULL DEFAULT 2,
    quality_score  REAL NOT NULL DEFAULT 0.5,
    items_assessed INTEGER NOT NULL DEFAULT 0,
    active         INTEGER NOT NULL DEFAULT 1,
    note           TEXT,
    added_at       TEXT DEFAULT (datetime('now')),
    last_assessed  TEXT,
    demoted_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_reddit_sources_domain ON reddit_sources(domain);

CREATE TABLE IF NOT EXISTS threads_search_terms (
    term           TEXT PRIMARY KEY,
    query_pack     TEXT,
    domain         TEXT NOT NULL DEFAULT 'other_signal',
    tier           INTEGER NOT NULL DEFAULT 2,
    quality_score  REAL NOT NULL DEFAULT 0.5,
    items_assessed INTEGER NOT NULL DEFAULT 0,
    active         INTEGER NOT NULL DEFAULT 1,
    added_at       TEXT DEFAULT (datetime('now')),
    last_assessed  TEXT,
    demoted_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_threads_terms_domain ON threads_search_terms(domain);

CREATE TABLE IF NOT EXISTS source_quality_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type    TEXT NOT NULL,
    source_key     TEXT NOT NULL,
    assessed_at    TEXT NOT NULL,
    score          REAL NOT NULL,
    items_count    INTEGER NOT NULL DEFAULT 0,
    relevance      REAL,
    engagement     REAL,
    novelty        REAL,
    confidence     REAL,
    notes          TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sqh_source ON source_quality_history(source_type, source_key);
CREATE INDEX IF NOT EXISTS idx_sqh_assessed ON source_quality_history(assessed_at DESC);
"""

MIGRATION_0010_CATEGORY_DEFAULTS = """
-- Note: SQLite doesn't support ALTER COLUMN to change defaults,
-- but we update existing rows with the legacy value.
UPDATE rss_event_analysis SET category = 'other_signal' WHERE category = 'other_interest';
UPDATE reddit_event_analysis SET category = 'other_signal' WHERE category = 'other_interest';
UPDATE threads_event_analysis SET category = 'other_signal' WHERE category = 'other_interest';
"""

MIGRATION_0011_CONTENT_SCHEMA = """
ALTER TABLE rss_event_analysis ADD COLUMN content_schema TEXT;
"""

MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("0001_core", MIGRATION_0001_CORE),
    ("0002_source_state", MIGRATION_0002_SOURCE_STATE),
    ("0003_token_usage", MIGRATION_0003_TOKEN_USAGE),
    ("0004_rss_analysis", MIGRATION_0004_RSS_ANALYSIS),
    ("0005_analyst", MIGRATION_0005_ANALYST),
    ("0006_delivery_attempts", MIGRATION_0006_DELIVERY_ATTEMPTS),
    ("0007_opportunity_bundles", MIGRATION_0007_OPPORTUNITY_BUNDLES),
    ("0008_cross_source_analysis", MIGRATION_0008_CROSS_SOURCE_ANALYSIS),
    ("0009_source_management", MIGRATION_0009_SOURCE_MANAGEMENT),
    ("0010_category_defaults", MIGRATION_0010_CATEGORY_DEFAULTS),
    ("0011_content_schema", MIGRATION_0011_CONTENT_SCHEMA),
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
