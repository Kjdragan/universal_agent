from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class MissionOutcome:
    status: str
    result_ref: Optional[str] = None
    message: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)


class VpClient(ABC):
    @abstractmethod
    async def run_mission(
        self,
        *,
        mission: dict[str, Any],
        workspace_root: Path,
    ) -> MissionOutcome:
        raise NotImplementedError
