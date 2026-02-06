import importlib


def test_exec_timeout_clamps_low_values(monkeypatch):
    monkeypatch.setenv("UA_HEARTBEAT_EXEC_TIMEOUT", "45")

    import universal_agent.heartbeat_service as heartbeat_service

    importlib.reload(heartbeat_service)
    assert heartbeat_service._resolve_exec_timeout_seconds() == 300


def test_exec_timeout_keeps_high_values(monkeypatch):
    monkeypatch.setenv("UA_HEARTBEAT_EXEC_TIMEOUT", "900")

    import universal_agent.heartbeat_service as heartbeat_service

    importlib.reload(heartbeat_service)
    assert heartbeat_service._resolve_exec_timeout_seconds() == 900
