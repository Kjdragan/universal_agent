"""
Universal Agent API package.

This module is intentionally **lightweight**: importing any `universal_agent.api.*`
submodule implicitly imports this package first, so heavy imports here can easily
create circular-import failures (notably with `universal_agent.gateway`).

We expose common symbols via lazy attribute resolution (PEP 562) to keep
import-time side effects minimal.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "app",
    "AgentBridge",
    "ProcessTurnBridge",
    "get_agent_bridge",
    "WebSocketEvent",
    "EventType",
    "SessionInfo",
    "ToolCallData",
    "ToolResultData",
    "WorkProductData",
]


def __getattr__(name: str) -> Any:  # pragma: no cover
    if name == "app":
        from universal_agent.api.server import app

        return app

    if name in {"AgentBridge", "get_agent_bridge"}:
        from universal_agent.api.agent_bridge import AgentBridge, get_agent_bridge

        return AgentBridge if name == "AgentBridge" else get_agent_bridge

    if name == "ProcessTurnBridge":
        from universal_agent.api.process_turn_bridge import ProcessTurnBridge

        return ProcessTurnBridge

    if name in {
        "WebSocketEvent",
        "EventType",
        "SessionInfo",
        "ToolCallData",
        "ToolResultData",
        "WorkProductData",
    }:
        from universal_agent.api import events as _events

        return getattr(_events, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
