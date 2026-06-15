from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass, field
import logging
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
    ``idle_timeout_seconds`` (when > 0) **while no tool is in flight**, the run
    is treated as hung: the generator is closed (propagating ``GeneratorExit``
    into the engine for cleanup) and ``error_text`` is set to a
    ``no_progress_timeout`` message. This is deliberately idle-based, not a
    wall-clock cap — a run that keeps emitting events is never cut off, and a
    long-running tool (build/test) that emits a ``tool_call`` then nothing until
    its ``tool_result`` is exempt from the idle kill (tools carry their own
    timeouts). The caller's ``adapter.close()`` still performs the full
    subprocess teardown.

    Shares the canonical ``timeout_policy.LivenessWatchdog`` with the in-process
    adapter (``execution_engine.py::ProcessTurnAdapter.execute``) and the
    ``claude --print`` subprocess (``claude_cli_client.py::_monitor_cli_output``)
    so the no-progress semantics live in one place. See
    ``project_docs/02_execution_core/01_gateway_sessions_execution.md``.
    """
    # Imported lazily to avoid any import cycle through this central module.
    from universal_agent.agent_core import EventType
    from universal_agent.timeout_policy import LivenessWatchdog

    final_text = ""
    error_text: Optional[str] = None
    trace_id: Optional[str] = None

    watchdog = LivenessWatchdog(idle_kill_seconds=idle_timeout_seconds)
    agen = adapter.execute(prompt)
    try:
        while True:
            # Wait for the next event, but no longer than the soonest moment the
            # watchdog could decide to kill. seconds_until_due() returns inf when
            # the idle kill is disabled (idle_timeout_seconds <= 0) or suspended
            # by an in-flight tool → block until the next event.
            wait_s = watchdog.seconds_until_due()
            try:
                if wait_s == float("inf"):
                    event = await agen.__anext__()
                else:
                    event = await asyncio.wait_for(
                        agen.__anext__(), timeout=max(0.0, wait_s)
                    )
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                reason = watchdog.overdue()
                if reason:
                    logger.warning(
                        "VP SDK mission killed: %s (no_progress_timeout)", reason
                    )
                    error_text = f"mission killed: {reason} (no_progress_timeout)"
                    break
                continue  # woke early on a partial window — re-arm

            watchdog.note_activity(
                tool_started=event.type == EventType.TOOL_CALL,
                tool_finished=event.type == EventType.TOOL_RESULT,
            )

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
