import sqlite3
import json
from datetime import datetime
import contextlib

class DiscordIntelligenceDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    @contextlib.contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS servers (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS channels (
                    id TEXT PRIMARY KEY,
                    server_id TEXT,
                    name TEXT,
                    tier TEXT DEFAULT 'C',
                    category TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    FOREIGN KEY (server_id) REFERENCES servers (id)
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    channel_id TEXT,
                    server_id TEXT,
                    author_id TEXT,
                    author_name TEXT,
                    content TEXT,
                    timestamp TIMESTAMP,
                    is_bot BOOLEAN,
                    reply_to_id TEXT,
                    has_attachments BOOLEAN,
                    processed_by_triage BOOLEAN DEFAULT 0,
                    FOREIGN KEY (channel_id) REFERENCES channels (id),
                    FOREIGN KEY (server_id) REFERENCES servers (id)
                )
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_messages_unprocessed 
                ON messages(channel_id, processed_by_triage)
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT,
                    layer TEXT,
                    rule_matched TEXT,
                    severity TEXT,
                    action_taken TEXT,
                    notified BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (message_id) REFERENCES messages (id)
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS triage_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT,
                    start_time TIMESTAMP,
                    end_time TIMESTAMP,
                    messages_count INTEGER,
                    status TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS insights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id INTEGER,
                    topic TEXT,
                    summary TEXT,
                    sentiment TEXT,
                    urgency TEXT,
                    confidence FLOAT,
                    source_message_ids TEXT,
                    notified BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (batch_id) REFERENCES triage_batches (id)
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS scheduled_events (
                    id TEXT PRIMARY KEY,
                    server_id TEXT,
                    name TEXT,
                    description TEXT,
                    start_time TIMESTAMP,
                    end_time TIMESTAMP,
                    location TEXT,
                    status TEXT,
                    entity_type TEXT,
                    channel_id TEXT,
                    creator_name TEXT,
                    user_count INTEGER DEFAULT 0,
                    notified BOOLEAN DEFAULT 0,
                    audio_path TEXT,
                    transcript_path TEXT,
                    transcript_status TEXT DEFAULT 'none',
                    persist_audio BOOLEAN DEFAULT 0
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS knowledge_updates (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    summary TEXT,
                    file_path TEXT,
                    notified BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            # Migrations for existing databases: add notified columns if missing
            self._migrate_add_column(conn, 'signals', 'notified', 'BOOLEAN DEFAULT 0')
            self._migrate_add_column(conn, 'insights', 'notified', 'BOOLEAN DEFAULT 0')
            # Audio recording column migrations
            self._migrate_add_column(conn, 'scheduled_events', 'audio_path', 'TEXT')
            self._migrate_add_column(conn, 'scheduled_events', 'transcript_path', 'TEXT')
            self._migrate_add_column(conn, 'scheduled_events', 'transcript_status', "TEXT DEFAULT 'none'")
            self._migrate_add_column(conn, 'scheduled_events', 'persist_audio', 'BOOLEAN DEFAULT 0')
            # Structured event discovery columns (replaces regex-based event detection)
            self._migrate_add_column(conn, 'scheduled_events', 'entity_type', 'TEXT')
            self._migrate_add_column(conn, 'scheduled_events', 'channel_id', 'TEXT')
            self._migrate_add_column(conn, 'scheduled_events', 'creator_name', 'TEXT')
            self._migrate_add_column(conn, 'scheduled_events', 'user_count', 'INTEGER DEFAULT 0')
            # Digest event columns
            self._migrate_add_column(conn, 'scheduled_events', 'digest_generated', 'BOOLEAN DEFAULT 0')
            self._migrate_add_column(conn, 'scheduled_events', 'digest_content', 'TEXT')

    @staticmethod
    def _migrate_add_column(conn, table: str, column: str, typedef: str):
        """Safely add a column to an existing table if it doesn't exist."""
        try:
            cur = conn.execute(f"PRAGMA table_info({table})")
            columns = [row[1] for row in cur.fetchall()]
            if column not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {typedef}")
                conn.commit()
        except Exception:
            pass  # Column already exists or table doesn't exist yet

    def upsert_server(self, server_id: str, name: str):
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO servers (id, name, is_active)
                VALUES (?, ?, 1)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, is_active=1
            ''', (server_id, name))
            conn.commit()

    def upsert_channel(self, channel_id: str, server_id: str, name: str, category: str = None):
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO channels (id, server_id, name, category, is_active)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, category=excluded.category, is_active=1
            ''', (channel_id, server_id, name, category))
            conn.commit()

    def set_channel_tier(self, channel_id: str, tier: str):
        with self._get_conn() as conn:
            conn.execute('UPDATE channels SET tier = ? WHERE id = ?', (tier, channel_id))
            conn.commit()

    def get_tier_channels(self, tier: str):
        with self._get_conn() as conn:
            cur = conn.execute('SELECT * FROM channels WHERE tier = ? AND is_active = 1', (tier,))
            return [dict(row) for row in cur.fetchall()]
            
    def store_message(self, msg_id: str, channel_id: str, server_id: str, author_id: str, 
                      author_name: str, content: str, timestamp: datetime, is_bot: bool, 
                      reply_to_id: str, has_attachments: bool):
        with self._get_conn() as conn:
            conn.execute('''
                INSERT OR IGNORE INTO messages 
                (id, channel_id, server_id, author_id, author_name, content, timestamp, is_bot, reply_to_id, has_attachments)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (msg_id, channel_id, server_id, author_id, author_name, content, 
                  timestamp.isoformat(), is_bot, reply_to_id, has_attachments))
            conn.commit()

    def store_signal(self, message_id: str, layer: str, rule_matched: str, severity: str, action_taken: str = None):
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO signals (message_id, layer, rule_matched, severity, action_taken)
                VALUES (?, ?, ?, ?, ?)
            ''', (message_id, layer, rule_matched, severity, action_taken))
            conn.commit()

    def get_unprocessed_messages(self, channel_id: str, limit: int = 100):
        with self._get_conn() as conn:
            cur = conn.execute('''
                SELECT * FROM messages 
                WHERE channel_id = ? AND processed_by_triage = 0 
                ORDER BY timestamp ASC LIMIT ?
            ''', (channel_id, limit))
            return [dict(row) for row in cur.fetchall()]

    def mark_messages_processed(self, message_ids: list):
        if not message_ids:
            return
        with self._get_conn() as conn:
            placeholders = ','.join('?' * len(message_ids))
            conn.execute(f'''
                UPDATE messages SET processed_by_triage = 1 
                WHERE id IN ({placeholders})
            ''', message_ids)
            conn.commit()

    def create_triage_batch(self, channel_id: str, start_time: datetime, end_time: datetime, count: int, status: str):
        with self._get_conn() as conn:
            cur = conn.execute('''
                INSERT INTO triage_batches (channel_id, start_time, end_time, messages_count, status)
                VALUES (?, ?, ?, ?, ?)
            ''', (channel_id, start_time.isoformat(), end_time.isoformat(), count, status))
            conn.commit()
            return cur.lastrowid

    def store_insight(self, batch_id: int, topic: str, summary: str, sentiment: str, 
                      urgency: str, confidence: float, source_ids: list):
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO insights (batch_id, topic, summary, sentiment, urgency, confidence, source_message_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (batch_id, topic, summary, sentiment, urgency, confidence, json.dumps(source_ids)))
            conn.commit()

    def upsert_scheduled_event(self, event_id: str, server_id: str, name: str, description: str, 
                               start_time: datetime, end_time: datetime, location: str, status: str,
                               entity_type: str = None, channel_id: str = None,
                               creator_name: str = None, user_count: int = 0):
        with self._get_conn() as conn:
            end_val = end_time.isoformat() if end_time else None
            start_val = start_time.isoformat() if start_time else None
            conn.execute('''
                INSERT INTO scheduled_events (id, server_id, name, description, start_time, end_time,
                                              location, status, entity_type, channel_id, creator_name, user_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET 
                    name=excluded.name, description=excluded.description,
                    start_time=excluded.start_time, end_time=excluded.end_time,
                    location=excluded.location, status=excluded.status,
                    entity_type=excluded.entity_type, channel_id=excluded.channel_id,
                    creator_name=excluded.creator_name, user_count=excluded.user_count
            ''', (event_id, server_id, name, description, start_val, end_val, location, status,
                  entity_type, channel_id, creator_name, user_count))
            conn.commit()

    def mark_event_notified(self, event_id: str):
        with self._get_conn() as conn:
            conn.execute('UPDATE scheduled_events SET notified = 1 WHERE id = ?', (event_id,))
            conn.commit()

    def get_unnotified_signals(self, limit: int = 20):
        """Get signals that haven't been posted to Discord yet."""
        with self._get_conn() as conn:
            cur = conn.execute('''
                SELECT s.id, s.message_id, s.rule_matched, s.severity, s.created_at,
                       m.content, m.author_name, m.channel_id,
                       c.name as channel_name, srv.name as server_name
                FROM signals s
                JOIN messages m ON s.message_id = m.id
                LEFT JOIN channels c ON m.channel_id = c.id
                LEFT JOIN servers srv ON m.server_id = srv.id
                WHERE s.notified = 0
                ORDER BY s.created_at DESC LIMIT ?
            ''', (limit,))
            return [dict(row) for row in cur.fetchall()]

    def mark_signals_notified(self, signal_ids: list):
        if not signal_ids:
            return
        with self._get_conn() as conn:
            placeholders = ','.join('?' * len(signal_ids))
            conn.execute(f'UPDATE signals SET notified = 1 WHERE id IN ({placeholders})', signal_ids)
            conn.commit()

    def get_unnotified_insights(self, limit: int = 20):
        """Get insights that haven't been posted to Discord yet."""
        with self._get_conn() as conn:
            cur = conn.execute('''
                SELECT i.id, i.topic, i.summary, i.sentiment, i.urgency, i.confidence,
                       i.created_at, tb.channel_id,
                       c.name as channel_name, srv.name as server_name
                FROM insights i
                JOIN triage_batches tb ON i.batch_id = tb.id
                LEFT JOIN channels c ON tb.channel_id = c.id
                LEFT JOIN servers srv ON c.server_id = srv.id
                WHERE i.notified = 0
                ORDER BY i.created_at DESC LIMIT ?
            ''', (limit,))
            return [dict(row) for row in cur.fetchall()]

    def mark_insights_notified(self, insight_ids: list):
        if not insight_ids:
            return
        with self._get_conn() as conn:
            placeholders = ','.join('?' * len(insight_ids))
            conn.execute(f'UPDATE insights SET notified = 1 WHERE id IN ({placeholders})', insight_ids)
            conn.commit()

    def store_knowledge_update(self, update_id: str, title: str, summary: str, file_path: str):
        with self._get_conn() as conn:
            conn.execute('''
                INSERT OR IGNORE INTO knowledge_updates (id, title, summary, file_path)
                VALUES (?, ?, ?, ?)
            ''', (update_id, title, summary, file_path))
            conn.commit()

    def get_unnotified_knowledge_updates(self, limit: int = 10):
        with self._get_conn() as conn:
            cur = conn.execute('''
                SELECT * FROM knowledge_updates WHERE notified = 0 ORDER BY created_at ASC LIMIT ?
            ''', (limit,))
            return [dict(row) for row in cur.fetchall()]

    def mark_knowledge_updates_notified(self, ids: list[str]):
        if not ids: return
        with self._get_conn() as conn:
            placeholders = ','.join('?' * len(ids))
            conn.execute(f"UPDATE knowledge_updates SET notified = 1 WHERE id IN ({placeholders})", ids)
            conn.commit()

    # ── Audio Recording Management ──────────────────────────────────────

    def update_event_audio_path(self, event_id: str, audio_path: str):
        """Set the audio recording file path for an event."""
        with self._get_conn() as conn:
            conn.execute(
                'UPDATE scheduled_events SET audio_path = ? WHERE id = ?',
                (audio_path, event_id)
            )
            conn.commit()

    def update_event_transcript(self, event_id: str, transcript_path: str, status: str = 'complete'):
        """Set the transcript file path and status for an event."""
        with self._get_conn() as conn:
            conn.execute(
                'UPDATE scheduled_events SET transcript_path = ?, transcript_status = ? WHERE id = ?',
                (transcript_path, status, event_id)
            )
            conn.commit()

    def set_event_persist_audio(self, event_id: str, persist: bool = True):
        """Mark an event's audio for long-term retention (bypass 30-day cleanup)."""
        with self._get_conn() as conn:
            conn.execute(
                'UPDATE scheduled_events SET persist_audio = ? WHERE id = ?',
                (1 if persist else 0, event_id)
            )
            conn.commit()

    def get_events_pending_transcription(self) -> list[dict]:
        """Get events that have audio but no transcript yet."""
        with self._get_conn() as conn:
            cur = conn.execute('''
                SELECT * FROM scheduled_events
                WHERE audio_path IS NOT NULL
                  AND (transcript_status IS NULL OR transcript_status = 'none')
                ORDER BY start_time DESC
            ''')
            return [dict(row) for row in cur.fetchall()]

    def get_events_with_audio(self) -> list[dict]:
        """Get all events that have audio recordings."""
        with self._get_conn() as conn:
            cur = conn.execute('''
                SELECT * FROM scheduled_events
                WHERE audio_path IS NOT NULL
                ORDER BY start_time DESC
            ''')
            return [dict(row) for row in cur.fetchall()]

    def get_events_for_audio_cleanup(self, cutoff_iso: str) -> list[dict]:
        """Get events with audio older than cutoff that are not marked for persistence."""
        with self._get_conn() as conn:
            cur = conn.execute('''
                SELECT * FROM scheduled_events
                WHERE audio_path IS NOT NULL
                  AND persist_audio = 0
                  AND start_time < ?
                ORDER BY start_time ASC
            ''', (cutoff_iso,))
            return [dict(row) for row in cur.fetchall()]

