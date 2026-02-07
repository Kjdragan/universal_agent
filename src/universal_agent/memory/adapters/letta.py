from __future__ import annotations

from typing import Any

from universal_agent.memory.adapters.base import MemoryAdapter
from universal_agent.memory.memory_models import MemoryEntry


class LettaAdapter(MemoryAdapter):
    """Dormant adapter placeholder for Letta-backed memory integration."""

    @property
    def name(self) -> str:
        return "letta"

    def write_entry(
        self,
        entry: MemoryEntry,
        *,
        memory_class: str,
        importance: float = 0.7,
    ) -> bool:
        return False

    def search(
        self,
        query: str,
        *,
        memory_class: str,
        limit: int,
        strategy: str,
    ) -> list[dict[str, Any]]:
        return []

    def sync_session(
        self,
        *,
        session_id: str | None,
        transcript_path: str,
        force: bool = False,
    ) -> dict[str, Any]:
        return {"indexed": False, "reason": "adapter_off"}

