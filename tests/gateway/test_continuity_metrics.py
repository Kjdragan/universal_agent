import importlib
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def server_module():
    mocked_modules = {
        "universal_agent.durable.db": MagicMock(),
        "universal_agent.durable.migrations": MagicMock(),
        "universal_agent.heartbeat_service": MagicMock(),
        "universal_agent.cron_service": MagicMock(),
        "universal_agent.ops_service": MagicMock(),
        "universal_agent.hooks_service": MagicMock(),
        "logfire": MagicMock(),
    }

    original_gateway_server = sys.modules.pop("universal_agent.gateway_server", None)
    try:
        with patch.dict(sys.modules, mocked_modules, clear=False):
            with patch.dict(os.environ, {"UA_GATEWAY_PORT": "8002"}, clear=False):
                server = importlib.import_module("universal_agent.gateway_server")
                yield server
    finally:
        sys.modules.pop("universal_agent.gateway_server", None)
        if original_gateway_server is not None:
            sys.modules["universal_agent.gateway_server"] = original_gateway_server


@pytest.mark.asyncio
async def test_websocket_missing_session_metrics(server_module):
    mock_websocket = AsyncMock()
    mock_manager = MagicMock()
    mock_manager.connect = AsyncMock()
    mock_manager.disconnect = MagicMock()

    with patch.object(server_module, "manager", mock_manager):
        metrics = {}

        def fake_increment(name, amount=1):
            metrics[name] = metrics.get(name, 0) + amount

        with patch("universal_agent.gateway_server._increment_metric", side_effect=fake_increment):
            with patch("universal_agent.gateway_server._require_session_ws_auth", return_value=True):
                with patch("universal_agent.gateway_server.get_session", return_value=None):
                    with patch("universal_agent.gateway_server.get_gateway") as mock_get_gw:
                        mock_gw = MagicMock()
                        mock_gw.resume_session = AsyncMock(side_effect=ValueError("Not found"))
                        mock_get_gw.return_value = mock_gw

                        await server_module.websocket_stream(mock_websocket, "missing_session")

                        assert metrics.get("ws_attach_attempts", 0) == 0
                        assert metrics.get("ws_attach_failures", 0) == 0
                        assert metrics.get("resume_failures", 0) == 0
                        mock_websocket.close.assert_called_with(code=4004, reason="Session not found")
