from __future__ import annotations

from types import SimpleNamespace

from universal_agent import agent_setup as agent_setup_module


def _build_setup(tmp_path):
    setup = agent_setup_module.AgentSetup(
        workspace_dir=str(tmp_path),
        enable_skills=False,
        verbose=False,
    )
    setup._session = SimpleNamespace(mcp=SimpleNamespace(url="http://example-mcp"))
    return setup


def test_agent_setup_includes_notebooklm_mcp_when_enabled(monkeypatch, tmp_path):
    setup = _build_setup(tmp_path)

    monkeypatch.setattr(agent_setup_module, "create_sdk_mcp_server", lambda **kwargs: {"type": "sdk"})
    monkeypatch.setattr(agent_setup_module, "get_all_internal_tools", lambda enable_memory: [])
    monkeypatch.setattr(
        agent_setup_module,
        "build_notebooklm_mcp_server_config",
        lambda: {"type": "stdio", "command": "notebooklm-mcp", "args": [], "env": {}},
    )

    import universal_agent.services.gws_mcp_bridge as gws_bridge

    monkeypatch.setattr(gws_bridge, "build_gws_mcp_server_config", lambda: None)

    servers = setup._build_mcp_servers()

    assert "notebooklm-mcp" in servers
    assert servers["notebooklm-mcp"]["command"] == "notebooklm-mcp"


def test_agent_setup_excludes_notebooklm_mcp_when_disabled(monkeypatch, tmp_path):
    setup = _build_setup(tmp_path)

    monkeypatch.setattr(agent_setup_module, "create_sdk_mcp_server", lambda **kwargs: {"type": "sdk"})
    monkeypatch.setattr(agent_setup_module, "get_all_internal_tools", lambda enable_memory: [])
    monkeypatch.setattr(agent_setup_module, "build_notebooklm_mcp_server_config", lambda: None)

    import universal_agent.services.gws_mcp_bridge as gws_bridge

    monkeypatch.setattr(gws_bridge, "build_gws_mcp_server_config", lambda: None)

    servers = setup._build_mcp_servers()

    assert "notebooklm-mcp" not in servers
