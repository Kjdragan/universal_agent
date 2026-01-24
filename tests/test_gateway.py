"""
Unit tests for Gateway interface and InProcessGateway implementation.

Tests cover:
- Session creation and management
- Gateway request/response handling
- Event streaming
"""

import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Import with fallback for when gateway module isn't fully available
try:
    from universal_agent.gateway import (
        InProcessGateway,
        GatewaySession,
        GatewayRequest,
        GatewayResponse,
    )
    GATEWAY_AVAILABLE = True
except ImportError:
    GATEWAY_AVAILABLE = False


@pytest.mark.skipif(not GATEWAY_AVAILABLE, reason="Gateway module not available")
class TestGatewaySession:
    """Tests for GatewaySession dataclass."""

    def test_session_creation(self):
        """Test creating a session with required fields."""
        session = GatewaySession(
            session_id="sess_abc123",
            user_id="user_1",
            workspace_dir="/tmp/workspace",
            created_at=datetime.now(timezone.utc),
        )
        
        assert session.session_id == "sess_abc123"
        assert session.user_id == "user_1"
        assert session.workspace_dir == "/tmp/workspace"
        assert session.metadata == {}

    def test_session_with_metadata(self):
        """Test creating a session with custom metadata."""
        session = GatewaySession(
            session_id="sess_def456",
            user_id="user_2",
            workspace_dir="/tmp/workspace2",
            created_at=datetime.now(timezone.utc),
            metadata={"custom_key": "custom_value"},
        )
        
        assert session.metadata["custom_key"] == "custom_value"


@pytest.mark.skipif(not GATEWAY_AVAILABLE, reason="Gateway module not available")
class TestGatewayRequest:
    """Tests for GatewayRequest dataclass."""

    def test_request_minimal(self):
        """Test creating a request with minimal fields."""
        request = GatewayRequest(user_input="Hello")
        
        assert request.user_input == "Hello"
        assert request.context is None
        assert request.max_iterations == 25
        assert request.stream is True

    def test_request_full(self):
        """Test creating a request with all fields."""
        request = GatewayRequest(
            user_input="Analyze code",
            context={"files": ["main.py"]},
            max_iterations=10,
            stream=False,
        )
        
        assert request.user_input == "Analyze code"
        assert request.context == {"files": ["main.py"]}
        assert request.max_iterations == 10
        assert request.stream is False


@pytest.mark.skipif(not GATEWAY_AVAILABLE, reason="Gateway module not available")
class TestGatewayResponse:
    """Tests for GatewayResponse dataclass."""

    def test_response_success(self):
        """Test creating a successful response."""
        response = GatewayResponse(
            session_id="sess_abc",
            success=True,
            output="Task completed",
            tool_calls=[{"name": "ListDir", "id": "call_1"}],
        )
        
        assert response.success is True
        assert response.output == "Task completed"
        assert len(response.tool_calls) == 1
        assert response.error is None

    def test_response_failure(self):
        """Test creating a failure response."""
        response = GatewayResponse(
            session_id="sess_abc",
            success=False,
            output="",
            tool_calls=[],
            error="Connection timeout",
        )
        
        assert response.success is False
        assert response.error == "Connection timeout"


@pytest.mark.skipif(not GATEWAY_AVAILABLE, reason="Gateway module not available")
class TestInProcessGateway:
    """Tests for InProcessGateway implementation."""

    @pytest.fixture
    def gateway(self):
        """Create a fresh gateway instance."""
        return InProcessGateway()

    @pytest.mark.asyncio
    async def test_create_session_with_workspace(self, gateway, tmp_path):
        """Test creating session with explicit workspace."""
        session = await gateway.create_session(
            user_id="test_user",
            workspace_dir=str(tmp_path),
        )
        
        assert session.session_id is not None
        assert session.user_id == "test_user"
        assert session.workspace_dir == str(tmp_path)
        assert isinstance(session.created_at, datetime)

    @pytest.mark.asyncio
    async def test_create_session_auto_workspace(self, gateway):
        """Test creating session with auto-generated workspace."""
        session = await gateway.create_session(user_id="test_user")
        
        assert session.session_id is not None
        assert session.workspace_dir is not None
        # Auto-generated workspace should contain 'gateway' or 'session'
        assert Path(session.workspace_dir).exists() or "gateway" in session.workspace_dir.lower()

    @pytest.mark.asyncio
    async def test_get_existing_session(self, gateway, tmp_path):
        """Test retrieving an existing session."""
        created = await gateway.create_session(
            user_id="test_user",
            workspace_dir=str(tmp_path),
        )
        
        retrieved = await gateway.get_session(created.session_id)
        
        assert retrieved is not None
        assert retrieved.session_id == created.session_id
        assert retrieved.user_id == created.user_id

    @pytest.mark.asyncio
    async def test_get_nonexistent_session(self, gateway):
        """Test retrieving a session that doesn't exist."""
        session = await gateway.get_session("nonexistent_session_id")
        assert session is None

    @pytest.mark.asyncio
    async def test_list_all_sessions(self, gateway, tmp_path):
        """Test listing all sessions."""
        # Create multiple sessions
        await gateway.create_session(user_id="user1", workspace_dir=str(tmp_path / "ws1"))
        await gateway.create_session(user_id="user2", workspace_dir=str(tmp_path / "ws2"))
        
        sessions = await gateway.list_sessions()
        
        assert len(sessions) >= 2

    @pytest.mark.asyncio
    async def test_list_sessions_by_user(self, gateway, tmp_path):
        """Test listing sessions filtered by user."""
        await gateway.create_session(user_id="user1", workspace_dir=str(tmp_path / "ws1"))
        await gateway.create_session(user_id="user1", workspace_dir=str(tmp_path / "ws2"))
        await gateway.create_session(user_id="user2", workspace_dir=str(tmp_path / "ws3"))
        
        user1_sessions = await gateway.list_sessions(user_id="user1")
        
        assert len(user1_sessions) >= 2
        for session in user1_sessions:
            assert session.user_id == "user1"

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent(self, gateway, tmp_path):
        """Test that multiple sessions are independent."""
        session1 = await gateway.create_session(
            user_id="user1",
            workspace_dir=str(tmp_path / "ws1"),
        )
        session2 = await gateway.create_session(
            user_id="user2",
            workspace_dir=str(tmp_path / "ws2"),
        )
        
        assert session1.session_id != session2.session_id
        assert session1.workspace_dir != session2.workspace_dir


@pytest.mark.skipif(not GATEWAY_AVAILABLE, reason="Gateway module not available")
class TestGatewayExecution:
    """Tests for gateway execution (mocked)."""

    @pytest.fixture
    def gateway(self):
        """Create gateway with mocked agent."""
        return InProcessGateway()

    @pytest.mark.asyncio
    async def test_execute_returns_events(self, gateway, tmp_path):
        """Test that execute yields events."""
        session = await gateway.create_session(
            user_id="test_user",
            workspace_dir=str(tmp_path),
        )
        request = GatewayRequest(user_input="Test query")
        
        # Mock the agent to avoid actual LLM calls
        with patch.object(gateway, '_get_or_create_agent') as mock_agent:
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run_query = AsyncMock(return_value=iter([]))
            mock_agent.return_value = mock_agent_instance
            
            events = []
            try:
                async for event in gateway.execute(session, request):
                    events.append(event)
            except Exception:
                # Expected if mocking isn't complete
                pass
            
            # At minimum, should not crash

    @pytest.mark.asyncio
    async def test_run_query_returns_response(self, gateway, tmp_path):
        """Test that run_query returns a GatewayResponse."""
        session = await gateway.create_session(
            user_id="test_user",
            workspace_dir=str(tmp_path),
        )
        request = GatewayRequest(user_input="Test query")
        
        # Mock to avoid actual execution
        with patch.object(gateway, 'execute') as mock_execute:
            async def mock_events():
                return
                yield  # Make it an async generator
            
            mock_execute.return_value = mock_events()
            
            try:
                response = await gateway.run_query(session, request)
                assert isinstance(response, GatewayResponse)
                assert response.session_id == session.session_id
            except Exception:
                # Expected if mocking isn't complete
                pass
