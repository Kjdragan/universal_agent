"""
Tests for LanceDB vector memory backend.
"""

import os
import shutil
import tempfile
import pytest

if (os.getenv("RUN_LANCEDB_TESTS", "") or "").strip().lower() not in {"1", "true", "yes"}:
    # LanceDB can hard-crash the interpreter on some CPUs due to native extension
    # instruction set requirements. Keep these tests opt-in so `pytest` is safe by default.
    pytest.skip(
        "Skipping LanceDB tests (set RUN_LANCEDB_TESTS=1 to enable).",
        allow_module_level=True,
    )

from universal_agent.memory.lancedb_backend import LanceDBMemory
from universal_agent.memory.embeddings import (
    EmbeddingProvider,
    SentenceTransformerEmbeddings,
    get_embedding_provider,
)


class TestEmbeddingProviders:
    """Test embedding provider functionality."""

    def test_sentence_transformer_embed(self):
        """Test that SentenceTransformer produces embeddings."""
        provider = SentenceTransformerEmbeddings(model="all-MiniLM-L6-v2", device="cpu")
        embedding = provider.embed("Hello world")
        
        assert isinstance(embedding, list)
        assert len(embedding) == provider.dimensions
        assert all(isinstance(x, float) for x in embedding)

    def test_sentence_transformer_batch(self):
        """Test batch embedding."""
        provider = SentenceTransformerEmbeddings(model="all-MiniLM-L6-v2", device="cpu")
        embeddings = provider.embed_batch(["Hello", "World", "Test"])
        
        assert len(embeddings) == 3
        assert all(len(e) == provider.dimensions for e in embeddings)

    def test_get_embedding_provider_default(self):
        """Test factory function returns SentenceTransformers by default."""
        provider = get_embedding_provider()
        assert isinstance(provider, SentenceTransformerEmbeddings)


class TestLanceDBMemory:
    """Test LanceDB memory backend."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database directory."""
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "test_lancedb")
        yield db_path
        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def memory(self, temp_db):
        """Create a LanceDB memory instance with test embeddings."""
        provider = SentenceTransformerEmbeddings(model="all-MiniLM-L6-v2", device="cpu")
        return LanceDBMemory(temp_db, embedding_provider=provider)

    def test_store_and_retrieve(self, memory):
        """Test basic store and search."""
        # Store a memory
        entry_id = memory.store(
            "We decided to use PostgreSQL for the database",
            category="decision",
            session_id="test-session-1",
        )
        
        assert entry_id is not None
        assert memory.count() == 1

        # Search for it
        results = memory.search("What database did we choose?", limit=5)
        
        assert len(results) >= 1
        assert results[0].text == "We decided to use PostgreSQL for the database"
        assert results[0].category == "decision"

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

    def test_category_assignment(self, memory):
        """Test that categories are stored correctly."""
        memory.store("I prefer TypeScript over JavaScript", category="preference")
        memory.store("API endpoint: https://api.example.com", category="entity")
        
        results = memory.search("TypeScript", limit=1)
        assert results[0].category == "preference"
        
        results = memory.search("API endpoint", limit=1)
        assert results[0].category == "entity"


class TestMemoryStoreIntegration:
    """Test integration with memory_store.py."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_memory_store_uses_lancedb(self, temp_workspace, monkeypatch):
        """Test that append_memory_entry uses LanceDB when configured."""
        # Set feature flags
        monkeypatch.setenv("UA_MEMORY_INDEX", "vector")
        monkeypatch.setenv("UA_MEMORY_BACKEND", "lancedb")
        
        from universal_agent.memory.memory_store import append_memory_entry
        from universal_agent.memory.memory_models import MemoryEntry
        from datetime import datetime
        
        entry = MemoryEntry(
            entry_id="test-1",
            session_id="test-session",
            timestamp=datetime.utcnow().isoformat(),
            source="test",
            content="This is a test memory entry for LanceDB integration",
            tags=["test"],
            summary="Test entry",
        )
        
        paths = append_memory_entry(temp_workspace, entry)
        
        # Verify LanceDB directory was created
        lancedb_path = os.path.join(temp_workspace, "memory", "lancedb")
        assert os.path.exists(lancedb_path)
