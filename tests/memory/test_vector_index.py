import os
from pathlib import Path

from universal_agent.memory.memory_store import append_memory_entry
from universal_agent.memory.memory_models import MemoryEntry
from universal_agent.tools.memory import ua_memory_search


def test_vector_index_search(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_MEMORY_INDEX", "vector")
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))

    entry = MemoryEntry(content="Favorite color is teal", source="test")
    append_memory_entry(str(tmp_path), entry)

    db_path = Path(tmp_path) / "memory" / "vector_index.sqlite"
    assert db_path.exists()

    results = ua_memory_search("favorite color", limit=3)
    assert "teal" in results.lower()
