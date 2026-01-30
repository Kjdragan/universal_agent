import os
import sqlite3
import uuid
from datetime import datetime
from typing import List, Optional
import chromadb
from .models import MemoryBlock, ArchivalItem

class StorageManager:
    """
    Hybrid storage manager:
    - SQLite for Core Memory (fast, structured, low latency)
    - ChromaDB for Archival Memory (semantic search)
    """
    
    def __init__(self, storage_dir: str):
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)
        
        # --- SQLite Setup (Core Memory) ---
        self.sqlite_path = os.path.join(storage_dir, "agent_core.db")
        self._init_sqlite()
        
        # --- ChromaDB Setup (Archival Memory) ---
        self.chroma_path = os.path.join(storage_dir, "chroma_db")
        # Initialize persistent client - Chroma handles default embeddings automatically using sentence-transformers
        self.chroma_client = chromadb.PersistentClient(path=self.chroma_path)
        
        # Get or create collection
        self.collection = self.chroma_client.get_or_create_collection(
            name="archival_memory"
        )

    def _init_sqlite(self):
        """Initialize SQLite tables for core memory blocks."""
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        
        # Table for Memory Blocks
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS core_blocks (
            label TEXT PRIMARY KEY,
            value TEXT,
            description TEXT,
            is_editable BOOLEAN,
            last_updated TEXT
        )
        ''')

        # Table for Processed Traces (Agent College)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS processed_traces (
            trace_id TEXT PRIMARY KEY,
            timestamp TEXT
        )
        ''')
        
        conn.commit()
        
        # Table for Archival FTS (Keyword Search)
        # Using FTS5 (assuming modern SQLite)
        try:
            cursor.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS archival_fts USING fts5(
                item_id UNINDEXED,
                content,
                tags
            )
            ''')
        except sqlite3.OperationalError:
            # Fallback for older SQLite if FTS5 missing (unlikely in this env)
            pass

        conn.close()

    # =========================================================================
    # CORE MEMORY METHODS (SQLite)
    # =========================================================================

    def get_core_memory(self) -> List[MemoryBlock]:
        """Retrieve all core memory blocks."""
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT label, value, description, is_editable, last_updated FROM core_blocks")
        rows = cursor.fetchall()
        conn.close()
        
        blocks = []
        for r in rows:
            blocks.append(MemoryBlock(
                label=r[0],
                value=r[1],
                description=r[2],
                is_editable=bool(r[3]),
                last_updated=datetime.fromisoformat(r[4]) if r[4] else datetime.now()
            ))
        return blocks

    def save_block(self, block: MemoryBlock):
        """Save or update a memory block."""
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT OR REPLACE INTO core_blocks (label, value, description, is_editable, last_updated)
        VALUES (?, ?, ?, ?, ?)
        ''', (
            block.label, 
            block.value, 
            block.description, 
            block.is_editable, 
            block.last_updated.isoformat()
        ))
        
        conn.commit()
        conn.close()

    def get_block(self, label: str) -> Optional[MemoryBlock]:
        """Get a specific block by label."""
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT label, value, description, is_editable, last_updated FROM core_blocks WHERE label = ?", (label,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return MemoryBlock(
                label=row[0],
                value=row[1],
                description=row[2],
                is_editable=bool(row[3]),
                last_updated=datetime.fromisoformat(row[4])
            )
        return None

    def add_processed_trace(self, trace_id: str):
        """Record a trace ID as processed."""
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT OR IGNORE INTO processed_traces (trace_id, timestamp)
        VALUES (?, ?)
        ''', (trace_id, datetime.now().isoformat()))
        
        conn.commit()
        conn.close()

    def is_trace_processed(self, trace_id: str) -> bool:
        """Check if a trace ID has already been processed."""
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT 1 FROM processed_traces WHERE trace_id = ?", (trace_id,))
        row = cursor.fetchone()
        conn.close()
        
        return row is not None

    # =========================================================================
    # ARCHIVAL MEMORY METHODS (ChromaDB)
    # =========================================================================

    def insert_archival(self, item: ArchivalItem) -> str:
        """
        Insert an item into archival memory.
        Returns the generated ID.
        """
        item_id = item.item_id or str(uuid.uuid4())
        
        # Metadata allows filtering by tags later
        metadata = {
            "timestamp": item.timestamp.isoformat(),
            "tags": ",".join(item.tags)
        }
        
        self.collection.add(
            documents=[item.content],
            metadatas=[metadata],
            ids=[item_id]
        )
        
        # Sync to FTS
        self._update_fts(item_id, item.content, metadata["tags"])
        
        return item_id

    def search_archival(self, query: str, limit: int = 5) -> List[ArchivalItem]:
        """
        Semantic search for archival items.
        """
        results = self.collection.query(
            query_texts=[query],
            n_results=limit
        )
        
        items = []
        # Results structure: {'ids': [['id1']], 'documents': [['doc1']], ...}
        if results['ids'] and len(results['ids']) > 0:
            ids = results['ids'][0]
            docs = results['documents'][0]
            metas = results['metadatas'][0]
            
            for i, doc in enumerate(docs):
                meta = metas[i] if metas else {}
                tags = meta.get("tags", "").split(",") if meta.get("tags") else []
                timestamp_str = meta.get("timestamp")
                timestamp = datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.now()
                
                items.append(ArchivalItem(
                    content=doc,
                    tags=[t for t in tags if t],
                    timestamp=timestamp,
                    item_id=ids[i]
                ))
                
        return items

    # =========================================================================
    # HYBRID SEARCH METHODS (Vector + FTS5)
    # =========================================================================

    def _update_fts(self, item_id: str, content: str, tags: str):
        """Sync item to SQLite FTS index."""
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        
        # FTS5 doesn't support INSERT OR REPLACE directly in same way, but DELETE+INSERT works
        cursor.execute("DELETE FROM archival_fts WHERE item_id = ?", (item_id,))
        cursor.execute("INSERT INTO archival_fts (item_id, content, tags) VALUES (?, ?, ?)", 
                      (item_id, content, tags))
        
        conn.commit()
        conn.close()

    def search_archival_hybrid(self, query: str, limit: int = 5) -> List[ArchivalItem]:
        """
        Hybrid search combining Vector (Chroma) and Keyword (SQLite FTS5).
        Uses Reciprocal Rank Fusion (RRF) to combine results.
        """
        # 1. Vector Search
        vector_results = self.collection.query(
            query_texts=[query],
            n_results=limit * 2  # Fetch more for fusion
        )
        
        vector_ids = []
        if vector_results['ids'] and len(vector_results['ids']) > 0:
            vector_ids = vector_results['ids'][0]
            
        # 2. Keyword Search (FTS)
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        
        # Simple FTS query
        # Sanitize query for FTS5 (remove special chars that might break syntax)
        safe_query = '"' + query.replace('"', '""') + '"'
        
        try:
            cursor.execute(f"""
                SELECT item_id, rank 
                FROM archival_fts 
                WHERE archival_fts MATCH ? 
                ORDER BY rank 
                LIMIT ?
            """, (safe_query, limit * 2))
            fts_rows = cursor.fetchall()
            fts_ids = [r[0] for r in fts_rows]
        except sqlite3.OperationalError:
            # Fallback if FTS fails (e.g. syntax error in query)
            fts_ids = []
            
        conn.close()
        
        # 3. Reciprocal Rank Fusion
        # RRF(d) = sum(1 / (k + rank(d)))
        k = 60
        scores = {}
        
        for rank, vid in enumerate(vector_ids):
            scores[vid] = scores.get(vid, 0) + (1 / (k + rank + 1))
            
        for rank, fid in enumerate(fts_ids):
            scores[fid] = scores.get(fid, 0) + (1 / (k + rank + 1))
            
        # Sort by score desc
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)[:limit]
        
        if not sorted_ids:
            return []
            
        # 4. Fetch final items from Chroma (as valid source of truth for content)
        final_results = self.collection.get(ids=sorted_ids)
        
        items = []
        # Re-order according to sorted_ids (Chroma .get doesn't guarantee order)
        # Create a map for O(1) lookup
        if final_results['ids']:
            id_map = {id: idx for idx, id in enumerate(final_results['ids'])}
            
            for sid in sorted_ids:
                if sid in id_map:
                    idx = id_map[sid]
                    meta = final_results['metadatas'][idx] if final_results['metadatas'] else {}
                    doc = final_results['documents'][idx] if final_results['documents'] else ""
                    
                    tags = meta.get("tags", "").split(",") if meta.get("tags") else []
                    timestamp_str = meta.get("timestamp")
                    timestamp = datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.now()
                    
                    items.append(ArchivalItem(
                        content=doc,
                        tags=[t for t in tags if t],
                        timestamp=timestamp,
                        item_id=sid
                    ))
                    
        return items
