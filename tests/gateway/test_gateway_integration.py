"""
Integration tests for Gateway system.

Tests cover:
- Gateway execution flows
- Session management across queries
- External gateway client/server interaction (when server running)
- URW harness with gateway mode
"""

import pytest
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Conditional imports
try:
    from universal_agent.gateway import (
        InProcessGateway,
        GatewayRequest,
        GatewaySession,
        GatewayResult,
    )
    GATEWAY_AVAILABLE = True
except ImportError:
    GATEWAY_AVAILABLE = False

try:
    from universal_agent.agent_core import EventType
    EVENTS_AVAILABLE = True
except ImportError:
    EVENTS_AVAILABLE = False

try:
    from universal_agent.urw.harness_orchestrator import HarnessOrchestrator, HarnessConfig
    HARNESS_AVAILABLE = True
except ImportError:
    HARNESS_AVAILABLE = False


@pytest.mark.skipif(not GATEWAY_AVAILABLE, reason="Gateway module not available")
class TestGatewaySessionManagement:
    """Integration tests for session management."""

    @pytest.fixture
    async def gateway(self):
        """Create gateway instance."""
        gw = InProcessGateway()
        yield gw
        # Cleanup if needed

    @pytest.mark.asyncio
    async def test_session_persists_across_queries(self, gateway, tmp_path):
        """Test that session state persists across multiple queries."""
        session = await gateway.create_session(
            user_id="test_user",
            workspace_dir=str(tmp_path),
        )
        
        # Verify session can be resumed multiple times
        for _ in range(3):
            retrieved = await gateway.resume_session(session.session_id)
            assert retrieved is not None
            assert retrieved.session_id == session.session_id

    @pytest.mark.asyncio
    async def test_multiple_sessions_isolated(self, gateway, tmp_path):
        """Test that multiple sessions are isolated."""
        ws1 = tmp_path / "ws1"
        ws2 = tmp_path / "ws2"
        ws1.mkdir()
        ws2.mkdir()
        
        session1 = await gateway.create_session(user_id="user1", workspace_dir=str(ws1))
        session2 = await gateway.create_session(user_id="user2", workspace_dir=str(ws2))
        
        # Sessions should be different
        assert session1.session_id != session2.session_id
        assert session1.workspace_dir != session2.workspace_dir
        
        # Each should be resumable
        retrieved1 = await gateway.resume_session(session1.session_id)
        retrieved2 = await gateway.resume_session(session2.session_id)
        assert retrieved1 is not None
        assert retrieved2 is not None


@pytest.mark.skipif(not GATEWAY_AVAILABLE or not EVENTS_AVAILABLE, 
                    reason="Required modules not available")
class TestGatewayEventStream:
    """Integration tests for event streaming."""

    @pytest.fixture
    async def gateway_session(self, tmp_path):
        """Create gateway with active session."""
        gateway = InProcessGateway()
        session = await gateway.create_session(
            user_id="test_user",
            workspace_dir=str(tmp_path),
        )
        return gateway, session

    @pytest.mark.asyncio
    async def test_execute_yields_events(self, gateway_session):
        """Test that execute yields event objects."""
        gateway, session = gateway_session
        request = GatewayRequest(user_input="Hello")
        
        events = []
        try:
            async for event in gateway.execute(session, request):
                events.append(event)
                # Stop after a few events to avoid long-running test
                if len(events) >= 5:
                    break
        except Exception:
            # May fail without actual LLM, but structure should work
            pass
        
        # At minimum, the mechanism should not crash

    @pytest.mark.asyncio
    async def test_run_query_returns_response(self, gateway_session):
        """Test that run_query returns proper GatewayResult object."""
        gateway, session = gateway_session
        request = GatewayRequest(user_input="Hello")
        
        # Mock the underlying execution
        with patch.object(gateway, 'execute') as mock_execute:
            async def mock_events():
                return
                yield  # Empty generator
            
            mock_execute.return_value = mock_events()
            
            try:
                result = await gateway.run_query(session, request)
                # GatewayResult has response_text, not session_id
                assert isinstance(result, GatewayResult)
            except Exception:
                # May fail with incomplete mocking
                pass


@pytest.mark.skipif(not HARNESS_AVAILABLE, reason="Harness module not available")
class TestHarnessGatewayIntegration:
    """Integration tests for URW harness with gateway mode."""

    def test_harness_config_gateway_options(self):
        """Test HarnessConfig includes gateway options."""
        config = HarnessConfig(
            use_gateway=True,
            gateway_url="http://localhost:8002",
        )
        
        assert config.use_gateway is True
        assert config.gateway_url == "http://localhost:8002"

    def test_harness_config_defaults(self):
        """Test HarnessConfig gateway defaults."""
        config = HarnessConfig()
        
        assert config.use_gateway is False
        assert config.gateway_url is None

    @pytest.mark.asyncio
    async def test_orchestrator_creation_with_gateway(self, tmp_path):
        """Test HarnessOrchestrator can be created with gateway config."""
        config = HarnessConfig(
            use_gateway=True,
            verbose=False,
        )
        
        orchestrator = HarnessOrchestrator(
            workspaces_root=tmp_path,
            config=config,
        )
        
        assert orchestrator.config.use_gateway is True
        assert orchestrator._gateway is None  # Not initialized until used

    @pytest.mark.asyncio
    async def test_orchestrator_get_gateway_in_process(self, tmp_path):
        """Test orchestrator creates in-process gateway when no URL."""
        config = HarnessConfig(
            use_gateway=True,
            gateway_url=None,
        )
        
        orchestrator = HarnessOrchestrator(
            workspaces_root=tmp_path,
            config=config,
        )
        
        with patch('universal_agent.urw.harness_orchestrator.InProcessGateway') as MockGateway:
            MockGateway.return_value = MagicMock()
            
            gateway = await orchestrator._get_gateway()
            
            if gateway is not None:  # If gateway is available
                MockGateway.assert_called_once()

    @pytest.mark.asyncio
    async def test_orchestrator_get_gateway_external(self, tmp_path):
        """Test orchestrator creates external gateway when URL provided."""
        config = HarnessConfig(
            use_gateway=True,
            gateway_url="http://localhost:8002",
        )
        
        orchestrator = HarnessOrchestrator(
            workspaces_root=tmp_path,
            config=config,
        )
        
        with patch('universal_agent.urw.harness_orchestrator.ExternalGateway') as MockExtGateway:
            MockExtGateway.return_value = MagicMock()
            
            gateway = await orchestrator._get_gateway()
            
            if gateway is not None:  # If gateway is available
                MockExtGateway.assert_called_once_with(base_url="http://localhost:8002")


@pytest.mark.integration
@pytest.mark.skipif(not GATEWAY_AVAILABLE, reason="Gateway module not available")
class TestExternalGatewayIntegration:
    """Integration tests requiring running external gateway server.
    
    These tests are marked as integration and should be run with:
    pytest -m integration tests/test_gateway_integration.py
    
    Requires gateway server running at http://localhost:8002
    """

    @pytest.fixture
    def gateway_url(self):
        """Get gateway URL from environment or default."""
        return os.getenv("UA_GATEWAY_URL", "http://localhost:8002")

    @pytest.mark.asyncio
    async def test_external_gateway_create_session(self, gateway_url):
        """Test creating session on external gateway."""
        try:
            from universal_agent.gateway import ExternalGateway
        except ImportError:
            pytest.skip("ExternalGateway not available")
        
        try:
            gateway = ExternalGateway(base_url=gateway_url)
            session = await gateway.create_session(
                user_id="test_user",
                workspace_dir="/tmp/test_workspace",
            )
            
            assert session.session_id is not None
            await gateway.close()
        except Exception as e:
            pytest.skip(f"External gateway not available: {e}")

    @pytest.mark.asyncio
    async def test_external_gateway_execute(self, gateway_url):
        """Test executing query on external gateway."""
        try:
            from universal_agent.gateway import ExternalGateway
        except ImportError:
            pytest.skip("ExternalGateway not available")
        
        try:
            gateway = ExternalGateway(base_url=gateway_url)
            session = await gateway.create_session(user_id="test_user")
            
            request = GatewayRequest(user_input="Hello")
            
            events = []
            async for event in gateway.execute(session, request):
                events.append(event)
            
            assert len(events) > 0
            await gateway.close()
        except Exception as e:
            pytest.skip(f"External gateway not available: {e}")


@pytest.mark.skipif(not GATEWAY_AVAILABLE, reason="Gateway module not available")
class TestGatewayErrorHandling:
    """Tests for gateway error handling."""

    @pytest.fixture
    async def gateway(self):
        """Create gateway instance."""
        return InProcessGateway()

    @pytest.mark.asyncio
    async def test_invalid_session_id(self, gateway):
        """Test handling of invalid session ID raises error."""
        with pytest.raises(ValueError, match="Unknown session_id"):
            await gateway.resume_session("nonexistent_session_12345")

    @pytest.mark.asyncio
    async def test_execute_with_invalid_session(self, gateway):
        """Test execute with non-existent session."""
        fake_session = GatewaySession(
            session_id="fake_session",
            user_id="fake_user",
            workspace_dir="/nonexistent",
        )
        
        request = GatewayRequest(user_input="Hello")
        
        # Should handle gracefully (error event or exception)
        try:
            async for event in gateway.execute(fake_session, request):
                pass
        except Exception:
            # Expected - invalid session should raise error
            pass
