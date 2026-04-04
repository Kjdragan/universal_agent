import json

import pytest

from universal_agent import gateway_server


class RecordingWebSocket:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def accept(self) -> None:
        return

    async def send_text(self, payload: str) -> None:
        self.payloads.append(json.loads(payload))


@pytest.mark.asyncio
async def test_broadcast_preserves_origin_session_for_global_agent_flow_watchers():
    manager = gateway_server.ConnectionManager()
    direct_socket = RecordingWebSocket()
    global_socket = RecordingWebSocket()

    await manager.connect("conn_direct", direct_socket, "session_demo")
    await manager.connect("conn_global", global_socket, "global_agent_flow")

    await manager.broadcast(
        "session_demo",
        {
            "type": "status",
            "data": {
                "status": "processing",
            },
        },
    )

    assert direct_socket.payloads == [
        {
            "type": "status",
            "data": {
                "status": "processing",
            },
        }
    ]

    assert global_socket.payloads == [
        {
            "type": "status",
            "data": {
                "status": "processing",
                "session_id": "session_demo",
                "source_session_id": "session_demo",
            },
            "session_id": "session_demo",
            "source_session_id": "session_demo",
        }
    ]
