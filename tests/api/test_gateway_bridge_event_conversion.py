from universal_agent.api.events import EventType
from universal_agent.api.gateway_bridge import GatewayBridge


def test_convert_gateway_event_merges_origin_session_into_payload():
    bridge = GatewayBridge("http://localhost:8002")

    event = bridge._convert_gateway_event(
        "status",
        {
            "data": {
                "status": "processing",
            },
            "session_id": "session_demo",
            "timestamp": 123.0,
        },
    )

    assert event is not None
    assert event.type == EventType.STATUS
    assert event.data["status"] == "processing"
    assert event.data["session_id"] == "session_demo"
    assert event.data["source_session_id"] == "session_demo"


def test_convert_gateway_heartbeat_event_preserves_origin_session_in_payload():
    bridge = GatewayBridge("http://localhost:8002")

    event = bridge._convert_gateway_event(
        "heartbeat_summary",
        {
            "data": {
                "summary": "Heartbeat ok",
            },
            "session_id": "cron_demo",
            "timestamp": 456.0,
        },
    )

    assert event is not None
    assert event.type == EventType.SYSTEM_EVENT
    assert event.data["type"] == "heartbeat_summary"
    assert event.data["payload"]["summary"] == "Heartbeat ok"
    assert event.data["payload"]["session_id"] == "cron_demo"
    assert event.data["payload"]["source_session_id"] == "cron_demo"
