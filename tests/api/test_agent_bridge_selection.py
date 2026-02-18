from universal_agent.api.process_turn_bridge import ProcessTurnBridge


def test_get_agent_bridge_prefers_process_turn(monkeypatch):
    monkeypatch.delenv("UA_GATEWAY_URL", raising=False)
    monkeypatch.delenv("UA_FORCE_LEGACY_AGENT_BRIDGE", raising=False)

    import universal_agent.api.agent_bridge as agent_bridge

    bridge = agent_bridge.get_agent_bridge()
    assert isinstance(bridge, ProcessTurnBridge)
    bridge_2 = agent_bridge.get_agent_bridge()
    assert isinstance(bridge_2, ProcessTurnBridge)
    assert bridge is not bridge_2


def test_get_agent_bridge_force_legacy(monkeypatch):
    monkeypatch.delenv("UA_GATEWAY_URL", raising=False)
    monkeypatch.setenv("UA_FORCE_LEGACY_AGENT_BRIDGE", "1")

    import universal_agent.api.agent_bridge as agent_bridge

    bridge = agent_bridge.get_agent_bridge()
    assert bridge.__class__.__name__ == "AgentBridge"
    bridge_2 = agent_bridge.get_agent_bridge()
    assert bridge_2.__class__.__name__ == "AgentBridge"
    assert bridge is not bridge_2


def test_get_agent_bridge_gateway_url(monkeypatch):
    monkeypatch.setenv("UA_GATEWAY_URL", "http://localhost:8002")
    monkeypatch.delenv("UA_FORCE_LEGACY_AGENT_BRIDGE", raising=False)

    import universal_agent.api.agent_bridge as agent_bridge

    bridge = agent_bridge.get_agent_bridge()
    assert bridge.__class__.__name__ == "GatewayBridge"
    bridge_2 = agent_bridge.get_agent_bridge()
    assert bridge_2.__class__.__name__ == "GatewayBridge"
    assert bridge is not bridge_2
