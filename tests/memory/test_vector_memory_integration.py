"""Integration tests for canonical vector-backed memory writes/search."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import universal_agent.memory.memory_store as memory_store
from universal_agent.memory.memory_models import MemoryEntry
from universal_agent.memory.memory_store import append_memory_entry
from universal_agent.memory.orchestrator import MemoryOrchestrator


@pytest.fixture
def temp_workspace() -> str:
    temp_dir = tempfile.mkdtemp()
    try:
        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_append_memory_entry_persists_and_is_searchable(temp_workspace: str):
    env_vars = {
        "UA_MEMORY_ENABLED": "1",
        "UA_MEMORY_PROVIDER": "auto",
        "AGENT_WORKSPACE_DIR": temp_workspace,
    }

    with patch.dict(os.environ, env_vars):
        # Reset singleton cache so backend writes into this workspace.
        memory_store._vector_memory = None

        entry = MemoryEntry(
            entry_id="integration-test-1",
            session_id="session-123",
            source="assistant",
            content="The user wants to refactor the login system using OAuth2.",
            tags=["refactor", "auth"],
            summary="Refactoring login to OAuth2",
            timestamp="2023-10-27T10:00:00Z",
        )

        append_memory_entry(temp_workspace, entry)

        memory_md_path = Path(temp_workspace) / "MEMORY.md"
        assert memory_md_path.exists()
        assert "OAuth2" in memory_md_path.read_text(encoding="utf-8")

        memory_dir = Path(temp_workspace) / "memory"
        assert memory_dir.exists()

        broker = MemoryOrchestrator(temp_workspace)
        hits = broker.search(query="OAuth2 refactor", limit=3, sources=["memory"])
        assert hits
        assert any("oauth2" in (hit.get("snippet") or "").lower() for hit in hits)
