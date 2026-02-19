from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.memory.adapters.memory_system import MemorySystemAdapter
from universal_agent.memory.paths import (
    resolve_agent_core_db_path,
    resolve_persist_directory,
    resolve_shared_memory_workspace,
)


def test_memory_path_resolvers_default_to_persistent_repo_locations(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("PERSIST_DIRECTORY", raising=False)
    monkeypatch.delenv("UA_SHARED_MEMORY_DIR", raising=False)

    workspace = (tmp_path / "AGENT_RUN_WORKSPACES" / "session_abc").resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    persist_dir = Path(resolve_persist_directory(str(workspace))).resolve()
    shared_dir = Path(resolve_shared_memory_workspace(str(workspace))).resolve()
    core_db = Path(resolve_agent_core_db_path(str(workspace))).resolve()

    assert persist_dir.name == "data"
    assert "Memory_System" in persist_dir.parts
    assert not str(persist_dir).startswith(str(workspace))

    assert shared_dir.name == "ua_shared_workspace"
    assert "Memory_System" in shared_dir.parts
    assert not str(shared_dir).startswith(str(workspace))

    assert core_db.parent == persist_dir
    assert core_db.name == "agent_core.db"


def test_memory_path_resolvers_honor_explicit_env(monkeypatch, tmp_path: Path):
    custom_persist = (tmp_path / "persistent_store").resolve()
    custom_shared = (tmp_path / "shared_workspace").resolve()
    monkeypatch.setenv("PERSIST_DIRECTORY", str(custom_persist))
    monkeypatch.setenv("UA_SHARED_MEMORY_DIR", str(custom_shared))

    assert Path(resolve_persist_directory(None)).resolve() == custom_persist
    assert Path(resolve_shared_memory_workspace(None)).resolve() == custom_shared
    assert Path(resolve_agent_core_db_path(None)).resolve() == custom_persist / "agent_core.db"


def test_memory_system_adapter_uses_persistent_fallback_not_session_local(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.delenv("PERSIST_DIRECTORY", raising=False)

    workspace = (tmp_path / "AGENT_RUN_WORKSPACES" / "session_local").resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    adapter = MemorySystemAdapter(str(workspace), state="shadow")
    if adapter._manager is None:
        pytest.skip("MemoryManager unavailable in this environment")

    resolved_persist = Path(resolve_persist_directory(str(workspace))).resolve()
    actual_storage = Path(adapter._manager.storage.storage_dir).resolve()

    assert actual_storage == resolved_persist
    assert not str(actual_storage).startswith(str(workspace))
