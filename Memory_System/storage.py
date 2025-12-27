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
        
        conn.commit()
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
