"""
Integration tests for the Vector Memory system.

Verifies the end-to-end flow from memory_store.py -> vector backend.
"""

import os
import shutil
import tempfile
import pytest
from unittest.mock import patch

from universal_agent.memory.memory_store import append_memory_entry, MemoryPaths
from universal_agent.memory.memory_models import MemoryEntry
from universal_agent.memory.chromadb_backend import get_memory as get_chroma_memory

@pytest.fixture
def temp_workspace():
    """Create a temporary workspace."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)

def test_append_memory_writes_to_chromadb(temp_workspace):
    """
    Verify that appending a memory entry writes to ChromaDB 
    when the vector backend is enabled.
    """
    # 1. Configure environment to use vector memory + ChromaDB
    env_vars = {
        "UA_MEMORY_INDEX": "vector",
        "UA_MEMORY_BACKEND": "chromadb",
        "UA_WORKSPACE_DIR": temp_workspace,
        "UA_EMBEDDING_PROVIDER": "sentence-transformers", # Use local for test
    }

    with patch.dict(os.environ, env_vars):
        # 2. Create a memory entry
        entry = MemoryEntry(
            entry_id="integration-test-1",
            session_id="session-123",
            source="assistant",
            content="The user wants to refactor the login system using OAuth2.",
            tags=["refactor", "auth"],
            summary="Refactoring login to OAuth2",
            timestamp="2023-10-27T10:00:00Z"
        )

        # 3. Call the main public API
        append_memory_entry(temp_workspace, entry)

        # 4. Verify Side Effects
        
        # Check Markdown file was written
        memory_md_path = os.path.join(temp_workspace, "MEMORY.md")
        assert os.path.exists(memory_md_path)
        with open(memory_md_path, "r") as f:
            content = f.read()
            assert "OAuth2" in content

        # Check ChromaDB persistence
        # We access the backend directly to verify wrote happened
        chroma_path = os.path.join(temp_workspace, "memory", "chromadb")
        assert os.path.exists(chroma_path)

        # Query ChromaDB to verify data
        # We construct a fresh memory instance pointing to the same path
        mem = get_chroma_memory(temp_workspace)
        results = mem.search("OAuth2 refactor", limit=1)
        
        assert len(results) == 1
        assert results[0].text == entry.content
        assert results[0].session_id == "session-123"
        assert results[0].source == "assistant"
