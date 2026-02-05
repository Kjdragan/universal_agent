"""
Tests for ChromaDB vector memory backend.
"""

import os
import shutil
import tempfile
import pytest

# Check if chromadb is available (it should be)
try:
    import chromadb
    from universal_agent.memory.chromadb_backend import ChromaDBMemory
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False

from universal_agent.memory.embeddings import (
    SentenceTransformerEmbeddings,
)

@pytest.mark.skipif(not CHROMA_AVAILABLE, reason="ChromaDB not installed")
class TestChromaDBMemory:
    """Test ChromaDB memory backend."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database directory."""
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "test_chromadb")
        yield db_path
        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def memory(self, temp_db):
        """Create a ChromaDB memory instance with test embeddings."""
        provider = SentenceTransformerEmbeddings(model="all-MiniLM-L6-v2", device="cpu")
        return ChromaDBMemory(temp_db, embedding_provider=provider)

    def test_store_and_retrieve(self, memory):
        """Test basic store and search."""
        # Store a memory
        entry_id = memory.store(
            "We decided to use PostgreSQL for the database",
            category="decision",
            session_id="test-session-1",
            source="user"
        )
        
        assert entry_id is not None
        assert memory.count() == 1

        # Search for it
        results = memory.search("What database did we choose?", limit=5)
        
        assert len(results) >= 1
        assert results[0].text == "We decided to use PostgreSQL for the database"
        assert results[0].category == "decision"
        assert results[0].score > 0.3

    def test_semantic_search(self, memory):
        """Test that semantic search finds related content."""
        # Store some memories
        memory.store("The user prefers dark mode themes")
        memory.store("We implemented JWT authentication")
        memory.store("The API uses REST architecture")

        # Search for related content (not exact match)
        results = memory.search("login security", limit=3)
        
        # Should find the authentication entry
        texts = [r.text for r in results]
        assert any("JWT" in t or "authentication" in t for t in texts)

    def test_duplicate_detection(self, memory):
        """Test that duplicates are not stored."""
        # Store original
        entry_id1 = memory.store("User likes Python programming")
        assert entry_id1 is not None
        
        # Try to store near-duplicate
        entry_id2 = memory.store("User likes Python programming", check_duplicates=True)
        
        # Should return None (duplicate detected)
        assert entry_id2 is None
        assert memory.count() == 1

    def test_delete(self, memory):
        """Test memory deletion."""
        entry_id = memory.store("Temporary note to delete")
        assert memory.count() == 1
        
        memory.delete(entry_id)
        assert memory.count() == 0

    def test_session_filter(self, memory):
        """Test filtering by session ID."""
        memory.store("Session 1 content", session_id="session-1")
        memory.store("Session 2 content", session_id="session-2")
        
        # Search in session 1 only
        results = memory.search("content", session_id="session-1", limit=5)
        
        assert len(results) == 1
        assert results[0].session_id == "session-1"

