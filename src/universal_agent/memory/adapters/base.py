from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from universal_agent.memory.memory_models import MemoryEntry


class MemoryAdapter(ABC):
    """Adapter contract for pluggable memory backends."""

    def __init__(self, workspace_dir: str, state: str = "off") -> None:
        self.workspace_dir = workspace_dir
        self.state = state

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def write_entry(
        self,
        entry: MemoryEntry,
        *,
        memory_class: str,
        importance: float = 0.7,
    ) -> bool:
        ...

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        memory_class: str,
        limit: int,
        strategy: str,
    ) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def sync_session(
        self,
        *,
        session_id: str | None,
        transcript_path: str,
        force: bool = False,
    ) -> dict[str, Any]:
        ...

