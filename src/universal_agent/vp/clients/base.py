from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MissionOutcome:
    status: str
    result_ref: Optional[str] = None
    message: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)


async def consume_adapter_events_with_idle_timeout(
    adapter: Any,
    prompt: str,
    *,
    idle_timeout_seconds: int,
) -> tuple[str, Optional[str], Optional[str]]:
    """Drive ``adapter.execute(prompt)`` and extract ``(final_text, error_text,
    trace_id)``, killing the run if it goes idle.

    Progress = any event emitted by the adapter. If no event arrives for
    ``idle_timeout_seconds`` (when > 0), the run is treated as hung: the
    generator is closed (propagating ``GeneratorExit`` into the engine for
    cleanup) and ``error_text`` is set to a ``no_progress_timeout`` message.
    This is deliberately idle-based, not a wall-clock cap — a run that keeps
    emitting events is never cut off here, so a long-but-productive mission
    runs freely while a genuinely stuck one is reaped. The caller's
    ``adapter.close()`` still performs the full subprocess teardown.

    Shared by the SDK-path VP clients (``ClaudeCodeClient`` /
    ``ClaudeGeneralistClient``) so the no-progress semantics live in one place.
    """
    # Imported lazily to avoid any import cycle through this central module.
    from universal_agent.agent_core import EventType

    final_text = ""
    error_text: Optional[str] = None
    trace_id: Optional[str] = None

    agen = adapter.execute(prompt)
    try:
        while True:
            try:
                if idle_timeout_seconds and idle_timeout_seconds > 0:
                    event = await asyncio.wait_for(
                        agen.__anext__(), timeout=idle_timeout_seconds
                    )
                else:
                    event = await agen.__anext__()
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                logger.warning(
                    "VP SDK mission killed: no progress for %ds (no_progress_timeout)",
                    idle_timeout_seconds,
                )
                error_text = (
                    f"mission killed: no progress for {idle_timeout_seconds}s "
                    f"(no_progress_timeout)"
                )
                break

            if event.type == EventType.TEXT and isinstance(event.data, dict):
                if event.data.get("final") is True:
                    final_text = str(event.data.get("text") or "")
            elif event.type == EventType.ERROR and isinstance(event.data, dict):
                error_text = str(
                    event.data.get("message")
                    or event.data.get("error")
                    or "mission failed"
                ).strip()
            elif event.type == EventType.ITERATION_END and isinstance(event.data, dict):
                trace_id = str(event.data.get("trace_id") or "") or None
    finally:
        try:
            await agen.aclose()
        except Exception:
            pass

    return final_text, error_text, trace_id


class VpClient(ABC):
    @abstractmethod
    async def run_mission(
        self,
        *,
        mission: dict[str, Any],
        workspace_root: Path,
    ) -> MissionOutcome:
        raise NotImplementedError
