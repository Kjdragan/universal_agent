from __future__ import annotations

from pathlib import Path

import universal_agent.memory.memory_store as memory_store
from universal_agent.memory.memory_store import append_memory_entry
from universal_agent.memory.memory_models import MemoryEntry
from universal_agent.tools.memory import memory_search


def test_memory_search_surfaces_vector_backed_hits(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))
    # Reset singleton cache so this test always writes into this workspace.
    memory_store._vector_memory = None

    entry = MemoryEntry(content="Favorite color is teal", source="test")
    append_memory_entry(str(tmp_path), entry)

    memory_dir = Path(tmp_path) / "memory"
    assert memory_dir.exists()
    assert any(
        candidate.exists()
        for candidate in [memory_dir / "vector_index.sqlite", memory_dir / "chromadb", memory_dir / "lancedb"]
    )

    results = memory_search("favorite color", limit=3)
    assert "Memory Search Results" in results
    assert "teal" in results.lower()
