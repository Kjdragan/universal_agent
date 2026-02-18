import asyncio

import pytest

from universal_agent import gateway_server


class SlowWebSocket:
    async def accept(self) -> None:
        return

    async def send_text(self, _payload: str) -> None:
        await asyncio.sleep(0.2)


@pytest.mark.asyncio
async def test_broadcast_evicts_stale_socket_on_send_timeout(monkeypatch):
    manager = gateway_server.ConnectionManager()
    websocket = SlowWebSocket()

    before_timeouts = int(gateway_server._observability_metrics.get("ws_send_timeouts", 0) or 0)
    before_evictions = int(gateway_server._observability_metrics.get("ws_stale_evictions", 0) or 0)

    monkeypatch.setattr(gateway_server, "WS_SEND_TIMEOUT_SECONDS", 0.01)

    await manager.connect("conn_1", websocket, "session_1")
    assert "conn_1" in manager.active_connections

    await manager.broadcast("session_1", {"type": "status", "data": {"status": "testing"}})

    assert "conn_1" not in manager.active_connections
    assert "session_1" not in manager.session_connections
    assert int(gateway_server._observability_metrics.get("ws_send_timeouts", 0) or 0) >= before_timeouts + 1
    assert int(gateway_server._observability_metrics.get("ws_stale_evictions", 0) or 0) >= before_evictions + 1
