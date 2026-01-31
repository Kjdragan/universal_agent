import importlib

from universal_agent.api.process_turn_bridge import ProcessTurnBridge


def _reset_bridges(agent_bridge_module):
    agent_bridge_module._agent_bridge = None
    agent_bridge_module._gateway_bridge = None
    agent_bridge_module._process_turn_bridge = None


def test_get_agent_bridge_prefers_process_turn(monkeypatch):
    monkeypatch.delenv("UA_GATEWAY_URL", raising=False)
    monkeypatch.delenv("UA_FORCE_LEGACY_AGENT_BRIDGE", raising=False)

    import universal_agent.api.agent_bridge as agent_bridge
    _reset_bridges(agent_bridge)

    bridge = agent_bridge.get_agent_bridge()
    assert isinstance(bridge, ProcessTurnBridge)


def test_get_agent_bridge_force_legacy(monkeypatch):
    monkeypatch.delenv("UA_GATEWAY_URL", raising=False)
    monkeypatch.setenv("UA_FORCE_LEGACY_AGENT_BRIDGE", "1")

    import universal_agent.api.agent_bridge as agent_bridge
    _reset_bridges(agent_bridge)

    bridge = agent_bridge.get_agent_bridge()
    assert bridge.__class__.__name__ == "AgentBridge"


def test_get_agent_bridge_gateway_url(monkeypatch):
    monkeypatch.setenv("UA_GATEWAY_URL", "http://localhost:8002")
    monkeypatch.delenv("UA_FORCE_LEGACY_AGENT_BRIDGE", raising=False)

    import universal_agent.api.agent_bridge as agent_bridge
    _reset_bridges(agent_bridge)

    bridge = agent_bridge.get_agent_bridge()
    assert bridge.__class__.__name__ == "GatewayBridge"
