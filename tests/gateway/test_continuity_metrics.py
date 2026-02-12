import sys
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

# PRE-MOCK heavy dependencies to avoid import side-effects
sys.modules["universal_agent.durable.db"] = MagicMock()
sys.modules["universal_agent.durable.migrations"] = MagicMock()
sys.modules["universal_agent.heartbeat_service"] = MagicMock()
sys.modules["universal_agent.cron_service"] = MagicMock()
sys.modules["universal_agent.ops_service"] = MagicMock()
sys.modules["universal_agent.hooks_service"] = MagicMock()
# sys.modules["universal_agent.main"] = MagicMock() # circular?
sys.modules["logfire"] = MagicMock()

# Also mock fastapi
mock_fastapi = MagicMock()
mock_fastapi.WebSocket = MagicMock
sys.modules["fastapi"] = mock_fastapi

# Now import server
# We might need to mock some env vars if they are read at module level
import os
with patch.dict(os.environ, {"UA_GATEWAY_PORT": "8002"}):
    import universal_agent.gateway_server as server

@pytest.mark.asyncio
async def test_websocket_missing_session_metrics():
    # Mock dependencies
    mock_websocket = AsyncMock()
    mock_manager = MagicMock()
    mock_manager.connect = AsyncMock()
    mock_manager.disconnect = MagicMock()
    
    # Patch manager on the server module
    with patch.object(server, "manager", mock_manager):
        
        # Mock increment metric to track calls
        metrics = {}
        def fake_increment(name, amount=1):
            metrics[name] = metrics.get(name, 0) + amount
            
        with patch("universal_agent.gateway_server._increment_metric", side_effect=fake_increment):
            with patch("universal_agent.gateway_server.get_session", return_value=None):
                with patch("universal_agent.gateway_server.get_gateway") as mock_get_gw:
                    # Mock gateway resume to raise ValueError
                    mock_gw = MagicMock()
                    mock_gw.resume_session = AsyncMock(side_effect=ValueError("Not found"))
                    mock_get_gw.return_value = mock_gw
                    
                    # Call the endpoint
                    await server.websocket_endpoint(mock_websocket, "missing_session")
                    
                    # Verify behaviour
                    # Should NOT count attempts or failures for missing session
                    assert metrics.get("ws_attach_attempts", 0) == 0
                    assert metrics.get("ws_attach_failures", 0) == 0
                    assert metrics.get("resume_failures", 0) == 0
                    
                    # Verify close
                    mock_websocket.close.assert_called_with(code=4004, reason="Session not found")
