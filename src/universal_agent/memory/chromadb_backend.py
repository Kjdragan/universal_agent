"""
ChromaDB vector memory backend.

Provides semantic search across agent memory using ChromaDB for storage
and configurable embedding providers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import uuid4

import chromadb
from chromadb.config import Settings

from .embeddings import EmbeddingProvider, get_embedding_provider


@dataclass
class MemorySearchResult:
    """Result from a memory search."""

    id: str
    text: str
    category: str
    importance: float
    session_id: Optional[str]
    source: Optional[str]
    timestamp: str
    score: float


class ChromaDBMemory:
    """
    ChromaDB-backed vector memory for semantic search.

    Features:
    - Automatic embedding generation
    - Semantic similarity search
    - Duplicate detection
    - Category-based organization
    """

    COLLECTION_NAME = "agent_memories"

    def __init__(
        self,
        db_path: str,
        embedding_provider: Optional[EmbeddingProvider] = None,
    ):
        """
        Initialize ChromaDB memory.

        Args:
            db_path: Path to ChromaDB database directory
            embedding_provider: Embedding provider (default: from env/config)
        """
        self.db_path = db_path
        self._embeddings = embedding_provider or get_embedding_provider()
        self._client: Optional[chromadb.PersistentClient] = None
        self._collection: Optional[chromadb.Collection] = None

    def _ensure_initialized(self) -> None:
        """Lazy initialization of database and collection."""
        if self._collection is not None:
            return

        os.makedirs(self.db_path, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=self.db_path,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def store(
        self,
        text: str,
        *,
        importance: float = 0.7,
        category: str = "other",
        session_id: Optional[str] = None,
        source: Optional[str] = None,
        timestamp: Optional[str] = None,
        check_duplicates: bool = True,
    ) -> Optional[str]:
        """
        Store a memory entry with automatic embedding.

        Returns:
            Entry ID if stored, None if duplicate detected
        """
        self._ensure_initialized()

        # Check for duplicates first
        if check_duplicates:
            existing = self.search(text, limit=1, min_score=0.95)
            if existing:
                return None  # Duplicate detected

        # Generate embedding
        vector = self._embeddings.embed(text)

        entry_id = str(uuid4())
        now = datetime.utcnow()
        ts = timestamp or now.isoformat()

        self._collection.add(
            ids=[entry_id],
            embeddings=[vector],
            documents=[text],
            metadatas=[{
                "importance": importance,
                "category": category,
                "session_id": session_id or "",
                "source": source or "",
                "timestamp": ts,
                "created_at": now.timestamp(),
            }],
        )
        return entry_id

    def search(
        self,
        query: str,
        limit: int = 5,
        min_score: float = 0.3,
        session_id: Optional[str] = None,
    ) -> list[MemorySearchResult]:
        """
        Search memories by semantic similarity.

        Returns:
            List of matching memories with scores
        """
        self._ensure_initialized()

        if self._collection.count() == 0:
            return []

        # Generate query embedding
        query_vector = self._embeddings.embed(query)

        # Build where filter
        where = None
        if session_id:
            where = {"session_id": session_id}

        results = self._collection.query(
            query_embeddings=[query_vector],
            n_results=limit,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        output = []
        if not results["ids"] or not results["ids"][0]:
            return output

        for i, entry_id in enumerate(results["ids"][0]):
            # ChromaDB returns L2 distance; convert to similarity (0-1)
            distance = results["distances"][0][i] if results["distances"] else 0
            score = 1 / (1 + distance)

            if score < min_score:
                continue

            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            doc = results["documents"][0][i] if results["documents"] else ""

            output.append(
                MemorySearchResult(
                    id=entry_id,
                    text=doc,
                    category=meta.get("category", "other"),
                    importance=meta.get("importance", 0.7),
                    session_id=meta.get("session_id") or None,
                    source=meta.get("source") or None,
                    timestamp=meta.get("timestamp", ""),
                    score=score,
                )
            )

        return output

    def delete(self, entry_id: str) -> bool:
        """Delete a memory entry by ID."""
        self._ensure_initialized()
        self._collection.delete(ids=[entry_id])
        return True

    def count(self) -> int:
        """Return total number of memories."""
        self._ensure_initialized()
        return self._collection.count()


# Module-level singleton for convenience
_default_memory: Optional[ChromaDBMemory] = None


def get_memory(workspace_dir: Optional[str] = None) -> ChromaDBMemory:
    """
    Get or create the default ChromaDB memory instance.
    """
    global _default_memory

    if _default_memory is None:
        if workspace_dir is None:
            workspace_dir = os.getenv("UA_WORKSPACE_DIR", os.getcwd())
        db_path = os.path.join(workspace_dir, "memory", "chromadb")
        _default_memory = ChromaDBMemory(db_path)

    return _default_memory
