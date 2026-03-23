import sqlite3
import os
import uuid
import json
from datetime import datetime
from threading import Lock

class LosslessDB:
    """
    SQLite backend for Lossless Context Management.
    Stores raw messages, hierarchical DAG summaries, and context mapping.
    """
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        # Ensure directory exists
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._lock = Lock()
        self._init_schema()
        
    def get_connection(self) -> sqlite3.Connection:
        # Use simple connections per thread, but we'll lock writes for safety
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                # Conversations
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS lcm_conversations (
                        id TEXT PRIMARY KEY,
                        session_id TEXT UNIQUE,
                        created_at DATETIME
                    )
                ''')
                # Messages
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS lcm_messages (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT,
                        seq INTEGER,
                        role TEXT,
                        content TEXT,
                        raw_blocks TEXT,
                        token_count INTEGER,
                        created_at DATETIME,
                        FOREIGN KEY(conversation_id) REFERENCES lcm_conversations(id)
                    )
                ''')
                # Index for quick retrieval by seq
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_msgs_conv_seq ON lcm_messages(conversation_id, seq)')
                
                # Summaries
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS lcm_summaries (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT,
                        depth INTEGER,
                        earliest_at DATETIME,
                        latest_at DATETIME,
                        descendant_count INTEGER,
                        token_count INTEGER,
                        content TEXT,
                        created_at DATETIME,
                        FOREIGN KEY(conversation_id) REFERENCES lcm_conversations(id)
                    )
                ''')
                
                # Summary DAG Links
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS lcm_summary_messages (
                        summary_id TEXT,
                        message_id TEXT,
                        PRIMARY KEY (summary_id, message_id),
                        FOREIGN KEY(summary_id) REFERENCES lcm_summaries(id),
                        FOREIGN KEY(message_id) REFERENCES lcm_messages(id)
                    )
                ''')
                
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS lcm_summary_parents (
                        summary_id TEXT,
                        parent_summary_id TEXT,
                        PRIMARY KEY (summary_id, parent_summary_id),
                        FOREIGN KEY(summary_id) REFERENCES lcm_summaries(id),
                        FOREIGN KEY(parent_summary_id) REFERENCES lcm_summaries(id)
                    )
                ''')
                
                # Ordered Context Items
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS lcm_context_items (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT,
                        ordinal INTEGER,
                        item_type TEXT, -- 'message' or 'summary'
                        reference_id TEXT,
                        FOREIGN KEY(conversation_id) REFERENCES lcm_conversations(id)
                    )
                ''')
                
                conn.commit()

    def get_or_create_conversation(self, session_id: str) -> str:
        """Returns the conversation ID for a session."""
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM lcm_conversations WHERE session_id = ?", (session_id,))
                row = cursor.fetchone()
                if row:
                    return row["id"]
                
                conv_id = f"conv_{uuid.uuid4().hex[:16]}"
                cursor.execute(
                    "INSERT INTO lcm_conversations (id, session_id, created_at) VALUES (?, ?, ?)",
                    (conv_id, session_id, datetime.now().isoformat())
                )
                conn.commit()
                return conv_id

    def insert_message(self, conversation_id: str, role: str, content: str, raw_blocks: list, token_count: int) -> dict:
        """Inserts a new raw message and appends it to the context_items."""
        msg_id = f"msg_{uuid.uuid4().hex[:16]}"
        now = datetime.now().isoformat()
        raw_json = json.dumps(raw_blocks) if raw_blocks else "[]"
        
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                # Get next seq
                cursor.execute("SELECT IFNULL(MAX(seq), 0) + 1 FROM lcm_messages WHERE conversation_id = ?", (conversation_id,))
                seq = cursor.fetchone()[0]
                
                cursor.execute(
                    "INSERT INTO lcm_messages (id, conversation_id, seq, role, content, raw_blocks, token_count, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (msg_id, conversation_id, seq, role, content, raw_json, token_count, now)
                )
                
                # Get next ordinal for context
                cursor.execute("SELECT IFNULL(MAX(ordinal), 0) + 1 FROM lcm_context_items WHERE conversation_id = ?", (conversation_id,))
                ordinal = cursor.fetchone()[0]
                
                ctx_id = f"ctx_{uuid.uuid4().hex[:12]}"
                cursor.execute(
                    "INSERT INTO lcm_context_items (id, conversation_id, ordinal, item_type, reference_id) VALUES (?, ?, ?, ?, ?)",
                    (ctx_id, conversation_id, ordinal, 'message', msg_id)
                )
                
                conn.commit()
                
        return {
            "id": msg_id,
            "seq": seq,
            "role": role,
            "content": content,
            "token_count": token_count,
            "created_at": now
        }

    def get_context_items(self, conversation_id: str) -> list:
        """Hydrates the active context list (summaries + fresh tail)."""
        # Returns ordered items with either 'message' or 'summary' loaded
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM lcm_context_items WHERE conversation_id = ? ORDER BY ordinal ASC", (conversation_id,))
            items = cursor.fetchall()
            
            hydrated = []
            for item in items:
                if item["item_type"] == "message":
                    cursor.execute("SELECT * FROM lcm_messages WHERE id = ?", (item["reference_id"],))
                    msg = cursor.fetchone()
                    if msg:
                        hydrated.append({"type": "message", "id": item["id"], "ordinal": item["ordinal"], "data": dict(msg)})
                else:
                    cursor.execute("SELECT * FROM lcm_summaries WHERE id = ?", (item["reference_id"],))
                    summ = cursor.fetchone()
                    if summ:
                        hydrated.append({"type": "summary", "id": item["id"], "ordinal": item["ordinal"], "data": dict(summ)})
                        
            return hydrated
