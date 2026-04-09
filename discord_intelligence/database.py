import sqlite3
import json
from datetime import datetime

class DiscordIntelligenceDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

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
                    notified BOOLEAN DEFAULT 0
                )
            ''')
            conn.commit()

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
                               start_time: datetime, end_time: datetime, location: str, status: str):
        with self._get_conn() as conn:
            end_val = end_time.isoformat() if end_time else None
            conn.execute('''
                INSERT INTO scheduled_events (id, server_id, name, description, start_time, end_time, location, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET 
                    name=excluded.name, description=excluded.description,
                    start_time=excluded.start_time, end_time=excluded.end_time,
                    location=excluded.location, status=excluded.status
            ''', (event_id, server_id, name, description, start_time.isoformat(), end_val, location, status))
            conn.commit()

    def mark_event_notified(self, event_id: str):
        with self._get_conn() as conn:
            conn.execute('UPDATE scheduled_events SET notified = 1 WHERE id = ?', (event_id,))
            conn.commit()
