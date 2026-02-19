from __future__ import annotations

import os
from pathlib import Path


def _repo_root() -> Path:
    # src/universal_agent/memory/paths.py -> repo root
    return Path(__file__).resolve().parents[3]


def _resolve_configured_path(raw_value: str | None, default_path: Path) -> Path:
    value = (raw_value or "").strip()
    if not value:
        return default_path
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    # Relative configured paths are anchored to repo root for deterministic behavior.
    return (_repo_root() / candidate).resolve()


def resolve_persist_directory(workspace_dir: str | None = None) -> str:
    """Return persistent storage dir for Memory_System (agent_core.db + chroma_db)."""
    del workspace_dir  # retained for call-site compatibility/future extension
    default_dir = (_repo_root() / "Memory_System" / "data").resolve()
    resolved = _resolve_configured_path(os.getenv("PERSIST_DIRECTORY"), default_dir)
    resolved.mkdir(parents=True, exist_ok=True)
    return str(resolved)


def resolve_shared_memory_workspace(workspace_dir: str | None = None) -> str:
    """Return shared workspace root for UA file memory artifacts."""
    del workspace_dir  # retained for call-site compatibility/future extension
    default_dir = (_repo_root() / "Memory_System" / "ua_shared_workspace").resolve()
    resolved = _resolve_configured_path(os.getenv("UA_SHARED_MEMORY_DIR"), default_dir)
    resolved.mkdir(parents=True, exist_ok=True)
    return str(resolved)


def resolve_agent_core_db_path(workspace_dir: str | None = None) -> str:
    """Return full path to persistent agent_core.db."""
    return str(Path(resolve_persist_directory(workspace_dir)) / "agent_core.db")
