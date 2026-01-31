"""
WebSocket Event Protocol for Universal Agent UI.

Defines the event types and schemas for bidirectional WebSocket communication
between the frontend and the Universal Agent backend.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
import json
import time


class EventType(str, Enum):
    """Types of events sent over WebSocket."""

    # Server -> Client events (from agent_core.py AgentEvent)
    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    STATUS = "status"
    HEARTBEAT = "heartbeat"
    AUTH_REQUIRED = "auth_required"
    ERROR = "error"
    SESSION_INFO = "session_info"
    ITERATION_END = "iteration_end"
    WORK_PRODUCT = "work_product"
    INPUT_REQUIRED = "input_required"
    INPUT_RESPONSE = "input_response"

    # Server -> Client control events
    CONNECTED = "connected"
    QUERY_COMPLETE = "query_complete"
    PONG = "pong"

    # Client -> Server events
    QUERY = "query"
    APPROVAL = "approval"
    PING = "ping"


@dataclass
class WebSocketEvent:
    """A WebSocket message with type, data, and timestamp."""

    type: EventType
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        """Convert event to JSON string."""
        return json.dumps(
            {
                "type": self.type.value,
                "data": self.data,
                "timestamp": self.timestamp,
            }
        )

    @classmethod
    def from_json(cls, json_str: str) -> "WebSocketEvent":
        """Create event from JSON string."""
        data = json.loads(json_str)
        return cls(
            type=EventType(data["type"]),
            data=data.get("data", {}),
            timestamp=data.get("timestamp", time.time()),
        )


@dataclass
class SessionInfo:
    """Session information sent on connection."""

    session_id: str
    workspace: str
    user_id: str
    session_url: Optional[str] = None
    logfire_enabled: bool = False


@dataclass
class ToolCallData:
    """Data for tool_call events."""

    name: str
    id: str
    input: dict
    time_offset: float


@dataclass
class ToolResultData:
    """Data for tool_result events."""

    tool_use_id: str
    is_error: bool
    content_preview: str
    content_size: int


@dataclass
class WorkProductData:
    """Data for work_product events."""

    content_type: str
    content: str
    filename: str
    path: str


@dataclass
class ApprovalRequest:
    """Data for approval_required events (URW phase approval)."""

    phase_id: str
    phase_name: str
    phase_description: str
    tasks: list[dict]
    requires_followup: bool = False


@dataclass
class ApprovalResponse:
    """Client approval response."""

    phase_id: str
    approved: bool
    followup_input: Optional[str] = None


def create_connected_event(session_info: SessionInfo) -> WebSocketEvent:
    """Create a connection established event."""
    return WebSocketEvent(
        type=EventType.CONNECTED,
        data={
            "message": "Connected to Universal Agent",
            "session": {
                "session_id": session_info.session_id,
                "workspace": session_info.workspace,
                "user_id": session_info.user_id,
                "session_url": session_info.session_url,
                "logfire_enabled": session_info.logfire_enabled,
            },
        },
    )


def create_text_event(text: str) -> WebSocketEvent:
    """Create a text streaming event."""
    return WebSocketEvent(
        type=EventType.TEXT,
        data={"text": text},
    )


def create_tool_call_event(name: str, tool_id: str, input_data: dict, time_offset: float) -> WebSocketEvent:
    """Create a tool call event."""
    return WebSocketEvent(
        type=EventType.TOOL_CALL,
        data={
            "name": name,
            "id": tool_id,
            "input": input_data,
            "time_offset": time_offset,
        },
    )


def create_tool_result_event(tool_use_id: str, is_error: bool, content_preview: str, content_size: int) -> WebSocketEvent:
    """Create a tool result event."""
    return WebSocketEvent(
        type=EventType.TOOL_RESULT,
        data={
            "tool_use_id": tool_use_id,
            "is_error": is_error,
            "content_preview": content_preview,
            "content_size": content_size,
        },
    )


def create_status_event(status: str, **extra_data) -> WebSocketEvent:
    """Create a status update event."""
    return WebSocketEvent(
        type=EventType.STATUS,
        data={"status": status, **extra_data},
    )


def create_error_event(error_message: str, error_details: Optional[dict] = None) -> WebSocketEvent:
    """Create an error event."""
    data = {"message": error_message}
    if error_details:
        data["details"] = error_details
    return WebSocketEvent(
        type=EventType.ERROR,
        data=data,
    )


def create_work_product_event(content_type: str, content: str, filename: str, path: str) -> WebSocketEvent:
    """Create a work product event."""
    return WebSocketEvent(
        type=EventType.WORK_PRODUCT,
        data={
            "content_type": content_type,
            "content": content,
            "filename": filename,
            "path": path,
        },
    )


def create_approval_required_event(phase_id: str, phase_name: str, phase_description: str, tasks: list) -> WebSocketEvent:
    """Create an approval request event for URW phases."""
    return WebSocketEvent(
        type=EventType.APPROVAL,
        data={
            "phase_id": phase_id,
            "phase_name": phase_name,
            "phase_description": phase_description,
            "tasks": tasks,
        },
    )


def create_input_required_event(question: str, category: str = "general", options: list = None) -> WebSocketEvent:
    """Create an input request event."""
    return WebSocketEvent(
        type=EventType.INPUT_REQUIRED,
        data={
            "question": question,
            "category": category,
            "options": options or [],
        },
    )


def create_input_response_event(response: str) -> WebSocketEvent:
    """Create an input response event from client."""
    return WebSocketEvent(
        type=EventType.INPUT_RESPONSE,
        data={"response": response},
    )
