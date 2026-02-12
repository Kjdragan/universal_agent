import asyncio
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from universal_agent.heartbeat_service import HeartbeatService, GatewaySession

class TestHeartbeatIdle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gateway = MagicMock()
        self.connection_manager = MagicMock()
        # Mock session_connections
        self.connection_manager.session_connections = {}
        
        self.service = HeartbeatService(
            gateway=self.gateway,
            connection_manager=self.connection_manager
        )
        
        # Enable service to run _process_session
        self.service.running = True

    async def test_session_active_connections(self):
        """Should NOT unregister if connections exist in metadata."""
        session = GatewaySession(
            session_id="s1", user_id="u1", workspace_dir="/tmp/ws1",
            metadata={"runtime": {"active_connections": 1, "last_activity_at": "2023-01-01T00:00:00"}}
        )
        self.service.register_session(session)
        
        # Run check
        result = self.service._check_session_idle(session)
        
        self.assertFalse(result)
        self.assertIn("s1", self.service.active_sessions)

    async def test_session_active_runs(self):
        """Should NOT unregister if runs exist."""
        session = GatewaySession(
            session_id="s2", user_id="u1", workspace_dir="/tmp/ws2",
            metadata={"runtime": {"active_connections": 0, "active_runs": 1, "last_activity_at": "2023-01-01T00:00:00"}}
        )
        self.service.register_session(session)
        
        # Run check
        result = self.service._check_session_idle(session)
        
        self.assertFalse(result)
        self.assertIn("s2", self.service.active_sessions)

    async def test_session_idle_timeout(self):
        """Should UNDREGISTER if idle > timeout."""
        # Mock time. 
        # last_activity = 1 hour ago
        last_activity = datetime.now() - timedelta(hours=1)
        
        session = GatewaySession(
            session_id="s3", user_id="u1", workspace_dir="/tmp/ws3",
            metadata={"runtime": {
                "active_connections": 0, 
                "active_runs": 0, 
                "last_activity_at": last_activity.isoformat()
            }}
        )
        self.service.register_session(session)
        
        # Set timeout to 5 mins
        with patch.dict(os.environ, {"UA_HEARTBEAT_IDLE_TIMEOUT": "300"}):
            result = self.service._check_session_idle(session)
            
        # Should be removed
        self.assertTrue(result)
        self.assertNotIn("s3", self.service.active_sessions)

    async def test_session_not_idle_yet(self):
        """Should NOT unregister if idle < timeout."""
        # last_activity = 1 minute ago
        last_activity = datetime.now() - timedelta(minutes=1)
        
        session = GatewaySession(
            session_id="s4", user_id="u1", workspace_dir="/tmp/ws4",
            metadata={"runtime": {
                "active_connections": 0, 
                "active_runs": 0, 
                "last_activity_at": last_activity.isoformat()
            }}
        )
        self.service.register_session(session)
        
        # Set timeout to 5 mins
        with patch.dict(os.environ, {"UA_HEARTBEAT_IDLE_TIMEOUT": "300"}):
            result = self.service._check_session_idle(session)
            
        self.assertFalse(result)
        self.assertIn("s4", self.service.active_sessions)

    async def test_connection_manager_fallback(self):
        """Should NOT unregister if metadata says 0 but connection_manager has connections."""
        session = GatewaySession(
            session_id="s5", user_id="u1", workspace_dir="/tmp/ws5",
            metadata={"runtime": {
                "active_connections": 0, # Stale metadata
                "active_runs": 0, 
                "last_activity_at": "2023-01-01T00:00:00"
            }}
        )
        self.service.register_session(session)
        
        # Mock actual connections
        self.connection_manager.session_connections = {"s5": {"conn1"}}
        
        result = self.service._check_session_idle(session)
        
        self.assertFalse(result)
        self.assertIn("s5", self.service.active_sessions)
