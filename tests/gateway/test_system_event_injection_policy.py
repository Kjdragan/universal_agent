from universal_agent.gateway_server import _should_inject_system_events_for_request


def test_user_requests_do_not_inject_system_events_by_default():
    assert _should_inject_system_events_for_request({"source": "user"}) is False
    assert _should_inject_system_events_for_request({}) is False


def test_system_lanes_inject_system_events():
    assert _should_inject_system_events_for_request({"source": "system"}) is True
    assert _should_inject_system_events_for_request({"source": "cron"}) is True
    assert _should_inject_system_events_for_request({"source": "heartbeat"}) is True
    assert _should_inject_system_events_for_request({"source": "autonomous"}) is True


def test_explicit_opt_in_injects_system_events():
    assert _should_inject_system_events_for_request(
        {"source": "user", "include_system_events": True}
    ) is True
