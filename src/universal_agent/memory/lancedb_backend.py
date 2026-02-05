"""
LanceDB vector memory backend.

Provides semantic search across agent memory using LanceDB for storage
and configurable embedding providers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import uuid4

import lancedb
from lancedb.pydantic import LanceModel, Vector

from .embeddings import EmbeddingProvider, get_embedding_provider


# We'll create the model dynamically based on embedding dimensions
def _create_memory_model(dim: int):
    """Create a LanceModel class with the correct vector dimensions."""

    class MemoryEntry(LanceModel):
        id: str
        text: str
        vector: Vector(dim)  # type: ignore
        importance: float = 0.7
        category: str = "other"
        session_id: Optional[str] = None
        source: Optional[str] = None
        timestamp: str = ""
        created_at: float = 0.0

    return MemoryEntry


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


class LanceDBMemory:
    """
    LanceDB-backed vector memory for semantic search.

    Features:
    - Automatic embedding generation
    - Semantic similarity search
    - Duplicate detection
    - Category-based organization
    """

    TABLE_NAME = "memories"

    def __init__(
        self,
        db_path: str,
        embedding_provider: Optional[EmbeddingProvider] = None,
    ):
        """
        Initialize LanceDB memory.

        Args:
            db_path: Path to LanceDB database directory
            embedding_provider: Embedding provider (default: from env/config)
        """
        self.db_path = db_path
        self._embeddings = embedding_provider or get_embedding_provider()
        self._db: Optional[lancedb.DBConnection] = None
        self._table: Optional[lancedb.table.Table] = None
        self._model_class: Optional[type] = None

    def _ensure_initialized(self) -> None:
        """Lazy initialization of database and table."""
        if self._table is not None:
            return

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._db = lancedb.connect(self.db_path)
        self._model_class = _create_memory_model(self._embeddings.dimensions)

        table_names = self._db.table_names()
        if self.TABLE_NAME in table_names:
            self._table = self._db.open_table(self.TABLE_NAME)
        else:
            # Create with schema (empty initial data)
            self._table = self._db.create_table(
                self.TABLE_NAME,
                schema=self._model_class,
                mode="create",
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

        Args:
            text: Text content to store
            importance: Importance score 0-1
            category: Category (preference, decision, entity, fact, other)
            session_id: Associated session ID
            source: Source of the memory (user, assistant, system)
            timestamp: ISO timestamp (default: now)
            check_duplicates: Skip if highly similar entry exists

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

        entry = {
            "id": entry_id,
            "text": text,
            "vector": vector,
            "importance": importance,
            "category": category,
            "session_id": session_id,
            "source": source,
            "timestamp": timestamp or now.isoformat(),
            "created_at": now.timestamp(),
        }

        self._table.add([entry])
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

        Args:
            query: Search query text
            limit: Maximum results to return
            min_score: Minimum similarity score (0-1)
            session_id: Filter by session ID (optional)

        Returns:
            List of matching memories with scores
        """
        self._ensure_initialized()

        if self._table.count_rows() == 0:
            return []

        # Generate query embedding
        query_vector = self._embeddings.embed(query)

        # Build search query
        search = self._table.search(query_vector).limit(limit)

        # Add session filter if specified
        if session_id:
            search = search.where(f"session_id = '{session_id}'")

        results = search.to_list()

        # Convert to results with similarity scores
        output = []
        for row in results:
            # LanceDB uses L2 distance; convert to similarity
            distance = row.get("_distance", 0)
            score = 1 / (1 + distance)

            if score < min_score:
                continue

            output.append(
                MemorySearchResult(
                    id=row["id"],
                    text=row["text"],
                    category=row["category"],
                    importance=row["importance"],
                    session_id=row.get("session_id"),
                    source=row.get("source"),
                    timestamp=row["timestamp"],
                    score=score,
                )
            )

        return output

    def delete(self, entry_id: str) -> bool:
        """
        Delete a memory entry by ID.

        Args:
            entry_id: Entry ID to delete

        Returns:
            True if deleted
        """
        self._ensure_initialized()
        self._table.delete(f"id = '{entry_id}'")
        return True

    def count(self) -> int:
        """Return total number of memories."""
        self._ensure_initialized()
        return self._table.count_rows()


# Module-level singleton for convenience
_default_memory: Optional[LanceDBMemory] = None


def get_memory(workspace_dir: Optional[str] = None) -> LanceDBMemory:
    """
    Get or create the default LanceDB memory instance.

    Args:
        workspace_dir: Workspace directory (uses env or default if not specified)

    Returns:
        LanceDBMemory instance
    """
    global _default_memory

    if _default_memory is None:
        if workspace_dir is None:
            workspace_dir = os.getenv("UA_WORKSPACE_DIR", os.getcwd())
        db_path = os.path.join(workspace_dir, "memory", "lancedb")
        _default_memory = LanceDBMemory(db_path)

    return _default_memory
