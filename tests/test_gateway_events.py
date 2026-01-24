"""
Unit tests for Gateway event types and EventType enum.

Tests cover:
- EventType enum values
- URW-specific event types
- Event data validation
"""

import pytest
from datetime import datetime, timezone

try:
    from universal_agent.agent_core import EventType, AgentEvent
    EVENTS_AVAILABLE = True
except ImportError:
    EVENTS_AVAILABLE = False


@pytest.mark.skipif(not EVENTS_AVAILABLE, reason="Events module not available")
class TestEventType:
    """Tests for EventType enum."""

    def test_core_event_types_exist(self):
        """Test that core event types are defined."""
        assert EventType.TEXT.value == "text"
        assert EventType.TOOL_CALL.value == "tool_call"
        assert EventType.TOOL_RESULT.value == "tool_result"
        assert EventType.THINKING.value == "thinking"
        assert EventType.STATUS.value == "status"
        assert EventType.ERROR.value == "error"

    def test_session_event_types_exist(self):
        """Test that session-related event types are defined."""
        assert EventType.SESSION_INFO.value == "session_info"
        assert EventType.ITERATION_END.value == "iteration_end"

    def test_output_event_types_exist(self):
        """Test that output-related event types are defined."""
        assert EventType.WORK_PRODUCT.value == "work_product"
        assert EventType.AUTH_REQUIRED.value == "auth_required"

    def test_urw_phase_event_types_exist(self):
        """Test that URW phase event types are defined (Stage 5)."""
        assert EventType.URW_PHASE_START.value == "urw_phase_start"
        assert EventType.URW_PHASE_COMPLETE.value == "urw_phase_complete"
        assert EventType.URW_PHASE_FAILED.value == "urw_phase_failed"
        assert EventType.URW_EVALUATION.value == "urw_evaluation"

    def test_event_type_is_string_enum(self):
        """Test that EventType inherits from str."""
        assert isinstance(EventType.TEXT, str)
        assert EventType.TEXT == "text"

    def test_all_event_types_unique(self):
        """Test that all event type values are unique."""
        values = [e.value for e in EventType]
        assert len(values) == len(set(values)), "Duplicate event type values found"


@pytest.mark.skipif(not EVENTS_AVAILABLE, reason="Events module not available")
class TestAgentEvent:
    """Tests for AgentEvent dataclass."""

    def test_event_creation_minimal(self):
        """Test creating an event with minimal fields."""
        event = AgentEvent(
            type=EventType.TEXT,
            data={"text": "Hello"},
        )
        
        assert event.type == EventType.TEXT
        assert event.data["text"] == "Hello"
        assert event.timestamp is not None

    def test_event_creation_with_timestamp(self):
        """Test creating an event with explicit timestamp."""
        ts = datetime(2026, 1, 24, 12, 0, 0, tzinfo=timezone.utc)
        event = AgentEvent(
            type=EventType.TEXT,
            data={"text": "Hello"},
            timestamp=ts,
        )
        
        assert event.timestamp == ts

    def test_text_event_data(self):
        """Test TEXT event data structure."""
        event = AgentEvent(
            type=EventType.TEXT,
            data={"text": "Hello, I'm Claude!"},
        )
        
        assert "text" in event.data
        assert isinstance(event.data["text"], str)

    def test_tool_call_event_data(self):
        """Test TOOL_CALL event data structure."""
        event = AgentEvent(
            type=EventType.TOOL_CALL,
            data={
                "id": "call_abc123",
                "name": "ListDir",
                "input": {"DirectoryPath": "/tmp"},
            },
        )
        
        assert event.data["id"] == "call_abc123"
        assert event.data["name"] == "ListDir"
        assert "input" in event.data

    def test_tool_result_event_data(self):
        """Test TOOL_RESULT event data structure."""
        event = AgentEvent(
            type=EventType.TOOL_RESULT,
            data={
                "tool_call_id": "call_abc123",
                "result": "file1.txt\nfile2.txt",
                "is_error": False,
            },
        )
        
        assert event.data["tool_call_id"] == "call_abc123"
        assert "result" in event.data
        assert event.data["is_error"] is False

    def test_error_event_data(self):
        """Test ERROR event data structure."""
        event = AgentEvent(
            type=EventType.ERROR,
            data={
                "error": "Connection timeout",
                "code": "TIMEOUT",
                "recoverable": True,
            },
        )
        
        assert event.data["error"] == "Connection timeout"
        assert event.data["code"] == "TIMEOUT"

    def test_urw_phase_start_event_data(self):
        """Test URW_PHASE_START event data structure."""
        event = AgentEvent(
            type=EventType.URW_PHASE_START,
            data={
                "phase_id": "phase_001",
                "phase_name": "Data Collection",
                "task_count": 5,
            },
        )
        
        assert event.data["phase_id"] == "phase_001"
        assert event.data["phase_name"] == "Data Collection"
        assert event.data["task_count"] == 5

    def test_urw_phase_complete_event_data(self):
        """Test URW_PHASE_COMPLETE event data structure."""
        event = AgentEvent(
            type=EventType.URW_PHASE_COMPLETE,
            data={
                "phase_id": "phase_001",
                "phase_name": "Data Collection",
                "success": True,
                "artifacts": ["data.csv", "summary.json"],
            },
        )
        
        assert event.data["success"] is True
        assert len(event.data["artifacts"]) == 2

    def test_urw_phase_failed_event_data(self):
        """Test URW_PHASE_FAILED event data structure."""
        event = AgentEvent(
            type=EventType.URW_PHASE_FAILED,
            data={
                "phase_id": "phase_001",
                "error": "API rate limit exceeded",
                "retry_count": 3,
            },
        )
        
        assert event.data["error"] == "API rate limit exceeded"
        assert event.data["retry_count"] == 3

    def test_urw_evaluation_event_data(self):
        """Test URW_EVALUATION event data structure."""
        event = AgentEvent(
            type=EventType.URW_EVALUATION,
            data={
                "phase_id": "phase_001",
                "is_complete": True,
                "missing_elements": [],
                "suggested_actions": [],
            },
        )
        
        assert event.data["is_complete"] is True
        assert event.data["missing_elements"] == []

    def test_event_serialization(self):
        """Test that events can be serialized to dict/JSON-compatible format."""
        event = AgentEvent(
            type=EventType.TOOL_CALL,
            data={"id": "call_1", "name": "Read"},
        )
        
        # Handle both datetime and float timestamps
        if hasattr(event.timestamp, 'isoformat'):
            ts_str = event.timestamp.isoformat()
        else:
            ts_str = str(event.timestamp)
        
        serialized = {
            "type": event.type.value,
            "data": event.data,
            "timestamp": ts_str,
        }
        
        assert serialized["type"] == "tool_call"
        assert isinstance(serialized["timestamp"], str)

    def test_event_type_comparison(self):
        """Test that event types can be compared."""
        event = AgentEvent(type=EventType.TEXT, data={})
        
        assert event.type == EventType.TEXT
        assert event.type == "text"
        assert event.type != EventType.ERROR
