import asyncio
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from universal_agent.heartbeat_service import GatewaySession, HeartbeatService


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
        with patch.dict(
            os.environ,
            {"UA_HEARTBEAT_IDLE_TIMEOUT": "300", "UA_HEARTBEAT_UNREGISTER_IDLE": "1"},
        ):
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
        with patch.dict(
            os.environ,
            {"UA_HEARTBEAT_IDLE_TIMEOUT": "300", "UA_HEARTBEAT_UNREGISTER_IDLE": "1"},
        ):
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

    async def test_idle_unregistration_enabled_by_default(self):
        """Should UNREGISTER idle sessions when UA_HEARTBEAT_UNREGISTER_IDLE is not set (defaults to True)."""
        last_activity = datetime.now() - timedelta(hours=2)
        session = GatewaySession(
            session_id="s6", user_id="u1", workspace_dir="/tmp/ws6",
            metadata={"runtime": {
                "active_connections": 0,
                "active_runs": 0,
                "last_activity_at": last_activity.isoformat()
            }}
        )
        self.service.register_session(session)

        with patch.dict(os.environ, {"UA_HEARTBEAT_IDLE_TIMEOUT": "300"}, clear=False):
            os.environ.pop("UA_HEARTBEAT_UNREGISTER_IDLE", None)
            result = self.service._check_session_idle(session)

        # Default is True — idle sessions should be unregistered
        self.assertTrue(result)
        self.assertNotIn("s6", self.service.active_sessions)

    async def test_daemon_session_timeout_ignores_active_runs_and_writes_crash_report(self):
        """Daemon sessions are killed after their daemon timeout even when active_runs is stuck."""
        callbacks: list[dict] = []

        def _timeout_callback(_session, payload):
            callbacks.append(payload)

        service = HeartbeatService(
            gateway=self.gateway,
            connection_manager=self.connection_manager,
            session_timeout_callback=_timeout_callback,
        )
        last_activity = datetime.now() - timedelta(hours=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "run_daemon_simone_heartbeat_20260429_010203_abcd1234"
            session = GatewaySession(
                session_id="daemon_simone_heartbeat",
                user_id="daemon",
                workspace_dir=str(workspace_dir),
                metadata={"runtime": {
                    "active_connections": 0,
                    "active_runs": 1,
                    "last_activity_at": last_activity.isoformat(),
                }},
            )
            service.register_session(session)

            with patch.dict(
                os.environ,
                {"UA_DAEMON_IDLE_TIMEOUT": "300", "UA_HEARTBEAT_UNREGISTER_IDLE": "1"},
            ):
                result = service._check_session_idle(session)

            crash_file = workspace_dir / "work_products" / "daemon_timeout_crash.json"
            self.assertTrue(result)
            self.assertNotIn("daemon_simone_heartbeat", service.active_sessions)
            self.assertTrue(crash_file.exists())
            payload = json.loads(crash_file.read_text())
            self.assertEqual(payload["session_id"], "daemon_simone_heartbeat")
            self.assertEqual(payload["workspace_dir"], str(workspace_dir))
            self.assertEqual(payload["timeout_threshold_seconds"], 300)
            self.assertEqual(payload["active_runs_reported"], 1)
            self.assertEqual(len(callbacks), 1)
            self.assertEqual(callbacks[0]["crash_report_path"], str(crash_file))

    async def test_daemon_session_with_recent_activity_is_not_killed(self):
        last_activity = datetime.now() - timedelta(minutes=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            session = GatewaySession(
                session_id="daemon_simone_heartbeat",
                user_id="daemon",
                workspace_dir=tmpdir,
                metadata={"runtime": {
                    "active_connections": 0,
                    "active_runs": 1,
                    "last_activity_at": last_activity.isoformat(),
                }},
            )
            self.service.register_session(session)

            with patch.dict(
                os.environ,
                {"UA_DAEMON_IDLE_TIMEOUT": "300", "UA_HEARTBEAT_UNREGISTER_IDLE": "1"},
            ):
                result = self.service._check_session_idle(session)

            self.assertFalse(result)
            self.assertIn("daemon_simone_heartbeat", self.service.active_sessions)
