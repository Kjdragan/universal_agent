"""
Universal Agent API Module

FastAPI server with WebSocket support for the modern React/Next.js UI.
"""

from universal_agent.api.server import app
from universal_agent.api.agent_bridge import AgentBridge, get_agent_bridge
from universal_agent.api.process_turn_bridge import ProcessTurnBridge
from universal_agent.api.events import (
    WebSocketEvent,
    EventType,
    SessionInfo,
    ToolCallData,
    ToolResultData,
    WorkProductData,
)

__all__ = [
    "app",
    "AgentBridge",
    "ProcessTurnBridge",
    "get_agent_bridge",
    "WebSocketEvent",
    "EventType",
    "SessionInfo",
]
