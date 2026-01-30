import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import os
import sys
from pathlib import Path

# Adjust path to find src
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from universal_agent.gateway_server import app, is_user_allowed

class TestDeploymentHardening(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        
    def test_allowlist_logic(self):
        """Test the pure logic of the allowlist checker."""
        # 1. Empty Allowlist (Default)
        with patch("universal_agent.gateway_server.ALLOWED_USERS", set()):
            self.assertTrue(is_user_allowed("any_user"))
            
        # 2. Configured Allowlist
        with patch("universal_agent.gateway_server.ALLOWED_USERS", {"verified_user"}):
            self.assertTrue(is_user_allowed("verified_user"))
            self.assertFalse(is_user_allowed("hacker"))
            
    @patch("universal_agent.main.runtime_db_conn")
    def test_health_check_deep(self, mock_db):
        """Test health check verifies DB connection."""
        # Case A: DB Healthy
        mock_db.execute.return_value = True
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")
        self.assertEqual(response.json()["db_status"], "connected")
        
        # Case B: DB Broken (Raises Exception)
        mock_db.execute.side_effect = Exception("Connection lost")
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "unhealthy")
        self.assertEqual(response.json()["db_status"], "error")

    @patch("universal_agent.gateway_server.get_gateway")
    @patch("universal_agent.gateway_server.ALLOWED_USERS", {"vip"})
    @patch("universal_agent.gateway_server.resolve_user_id")
    def test_create_session_enforcement(self, mock_resolve, mock_get_gateway):
        """Verify POST /sessions rejects unauthorized users."""
        mock_resolve.side_effect = lambda u: u or "default"
        
        # 1. Authorized
        response = self.client.post("/api/v1/sessions", json={"user_id": "vip"})
        # We expect it to try calling gateway, which is mocked. 
        # If it passed the check, it proceeds to gateway.create_session (which fails/mocks)
        # We just want to ensure it DOESN'T return 403.
        # Since we didn't mock gateway completely, let's just assert code != 403
        if response.status_code == 403:
            self.fail("Should have allowed 'vip'")
            
        # 2. Unauthorized
        response = self.client.post("/api/v1/sessions", json={"user_id": "random"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("Access denied", response.json()["detail"])

if __name__ == "__main__":
    unittest.main()
