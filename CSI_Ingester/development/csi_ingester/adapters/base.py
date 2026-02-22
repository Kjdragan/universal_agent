"""Adapter interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from csi_ingester.contract import CreatorSignalEvent


@dataclass(slots=True)
class RawEvent:
    source: str
    event_type: str
    payload: dict[str, Any]
    occurred_at: str


class SourceAdapter(ABC):
    @abstractmethod
    async def fetch_events(self) -> list[RawEvent]:
        """Fetch source events."""

    @abstractmethod
    def normalize(self, raw: RawEvent) -> CreatorSignalEvent:
        """Normalize source event into contract event."""

    @abstractmethod
    def get_dedupe_key(self, event: CreatorSignalEvent) -> str:
        """Compute stable dedupe key."""

