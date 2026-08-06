"""
WebSocket Event Protocol for Universal Agent UI.

Defines the event types and schemas for bidirectional WebSocket communication
between the frontend and the Universal Agent backend.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json
import time
from typing import Any, Optional


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
    SYSTEM_EVENT = "system_event"
    SYSTEM_PRESENCE = "system_presence"

    # Server -> Client control events
    CONNECTED = "connected"
    QUERY_COMPLETE = "query_complete"
    CANCELLED = "cancelled"
    PONG = "pong"

    # Client -> Server events
    QUERY = "query"
    APPROVAL = "approval"
    PING = "ping"
    CANCEL = "cancel"


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
    run_id: Optional[str] = None
    is_live_session: bool = True


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
    session_payload = {
        "session_id": session_info.session_id,
        "workspace": session_info.workspace,
        "workspace_dir": session_info.workspace,
        "user_id": session_info.user_id,
        "session_url": session_info.session_url,
        "logfire_enabled": session_info.logfire_enabled,
        "run_id": session_info.run_id,
        "is_live_session": session_info.is_live_session,
    }
    return WebSocketEvent(
        type=EventType.CONNECTED,
        data={
            "message": "Connected to Universal Agent",
            "session": session_payload,
            # Legacy flat aliases kept for older clients that still read from
            # the top-level payload instead of `data.session`.
            **session_payload,
        },
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
