from fastapi.testclient import TestClient

from universal_agent.api import server as api_server


def test_global_agent_flow_websocket_sends_connected_event(monkeypatch):
    monkeypatch.setattr(
        api_server,
        "_authenticate_dashboard_ws",
        lambda _websocket: api_server.DashboardAuthResult(
            authenticated=True,
            auth_required=True,
            owner_id="owner_primary",
        ),
    )
    monkeypatch.setattr(api_server, "_gateway_url", lambda: "")

    client = TestClient(api_server.app, raise_server_exceptions=False)

    with client.websocket_connect("/ws/agent?session_id=global_agent_flow") as websocket:
        payload = websocket.receive_json()

    assert payload["type"] == "connected"
    assert payload["data"]["session"]["session_id"] == "global_agent_flow"
    assert payload["data"]["session"]["workspace"] == "global"
    assert payload["data"]["session"]["is_live_session"] is False
