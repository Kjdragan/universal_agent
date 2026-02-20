from __future__ import annotations

from pathlib import Path

from universal_agent.memory.orchestrator import get_memory_orchestrator
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


def test_orchestrator_uses_shared_memory_workspace_for_persistence(monkeypatch, tmp_path: Path):
    shared_root = (tmp_path / "Memory_System" / "ua_shared_workspace").resolve()
    monkeypatch.setenv("UA_SHARED_MEMORY_DIR", str(shared_root))

    session_workspace = (tmp_path / "AGENT_RUN_WORKSPACES" / "session_xyz").resolve()
    session_workspace.mkdir(parents=True, exist_ok=True)

    broker = get_memory_orchestrator(workspace_dir=resolve_shared_memory_workspace(str(session_workspace)))
    assert Path(broker.workspace_dir).resolve() == shared_root

    written = broker.write(
        content="Persistent shared memory marker.",
        source="test",
        session_id="session_xyz",
        tags=["persist"],
        memory_class="long_term",
        importance=1.0,
    )
    assert written is not None
    assert (shared_root / "MEMORY.md").exists()
    assert not (session_workspace / "MEMORY.md").exists()
