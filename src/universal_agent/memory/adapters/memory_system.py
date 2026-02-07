from __future__ import annotations

import os
from typing import Any

from universal_agent.memory.adapters.base import MemoryAdapter
from universal_agent.memory.memory_models import MemoryEntry


class MemorySystemAdapter(MemoryAdapter):
    """Compatibility adapter for legacy Memory_System module (shadow use)."""

    @property
    def name(self) -> str:
        return "memory_system"

    def __init__(self, workspace_dir: str, state: str = "shadow") -> None:
        super().__init__(workspace_dir=workspace_dir, state=state)
        self._manager = None
        self._init_error = None
        try:
            from Memory_System.manager import MemoryManager

            storage_path = os.getenv("PERSIST_DIRECTORY", os.path.join(workspace_dir, "Memory_System_Data"))
            self._manager = MemoryManager(storage_dir=storage_path, workspace_dir=workspace_dir)
        except Exception as exc:  # pragma: no cover - optional dependency path
            self._init_error = str(exc)

    def write_entry(
        self,
        entry: MemoryEntry,
        *,
        memory_class: str,
        importance: float = 0.7,
    ) -> bool:
        if not self._manager:
            return False
        try:
            if memory_class == "session":
                session_id = entry.session_id or "unknown"
                self._manager.transcript_index(session_id, entry.content)
            else:
                self._manager.archival_memory_insert(entry.content, tags=",".join(entry.tags))
            return True
        except Exception:
            return False

    def search(
        self,
        query: str,
        *,
        memory_class: str,
        limit: int,
        strategy: str,
    ) -> list[dict[str, Any]]:
        # Kept intentionally minimal while adapter is shadow/off by default.
        if not self._manager:
            return []
        try:
            text = self._manager.archival_memory_search(query, limit=limit)
        except Exception:
            return []
        if not text or text.startswith("No relevant"):
            return []
        return [
            {
                "source": "memory_system",
                "memory_class": memory_class,
                "timestamp": "",
                "summary": text[:280],
                "preview": text[:280],
                "score": 0.0,
                "path": "",
                "session_id": None,
                "tags": [],
            }
        ]

    def sync_session(
        self,
        *,
        session_id: str | None,
        transcript_path: str,
        force: bool = False,
    ) -> dict[str, Any]:
        if not self._manager:
            return {"indexed": False, "reason": "manager_unavailable"}
        if not os.path.exists(transcript_path):
            return {"indexed": False, "reason": "transcript_missing"}
        try:
            with open(transcript_path, "r", encoding="utf-8", errors="replace") as handle:
                content = handle.read()
            if not content.strip():
                return {"indexed": False, "reason": "empty_transcript"}
            self._manager.transcript_index(session_id or "unknown", content[-20_000:])
            return {"indexed": True, "reason": "indexed"}
        except Exception:
            return {"indexed": False, "reason": "sync_failed"}

