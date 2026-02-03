from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional
import uuid


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MemoryEntry:
    content: str
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=_now_iso)
    source: str = "unknown"
    session_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    summary: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp,
            "source": self.source,
            "session_id": self.session_id,
            "tags": list(self.tags),
            "summary": self.summary,
            "content": self.content,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "MemoryEntry":
        return cls(
            entry_id=payload.get("entry_id") or str(uuid.uuid4()),
            timestamp=payload.get("timestamp") or _now_iso(),
            source=payload.get("source") or "unknown",
            session_id=payload.get("session_id"),
            tags=list(payload.get("tags") or []),
            summary=payload.get("summary"),
            content=payload.get("content", ""),
        )
