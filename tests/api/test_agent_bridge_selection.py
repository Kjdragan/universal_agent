from pathlib import Path

import pytest

from universal_agent.api.process_turn_bridge import ProcessTurnBridge
from universal_agent.durable.db import connect_runtime_db
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import upsert_run


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


def test_process_turn_bridge_lists_run_backed_workspaces(monkeypatch, tmp_path: Path):
    runtime_db = tmp_path / "runtime_state.db"
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(runtime_db))

    run_dir = tmp_path / "run_20260324_bridge"
    run_dir.mkdir()

    conn = connect_runtime_db(str(runtime_db))
    ensure_schema(conn)
    upsert_run(
        conn,
        run_id="run_bridge_1",
        entrypoint="unit_test",
        run_spec={"workspace_dir": str(run_dir.resolve())},
        status="completed",
        workspace_dir=str(run_dir.resolve()),
        run_kind="bridge_test",
        trigger_source="unit",
    )
    conn.commit()
    conn.close()

    bridge = ProcessTurnBridge()
    bridge.gateway._workspace_base = tmp_path
    monkeypatch.setattr(bridge.gateway, "list_live_sessions", lambda: [])

    sessions = bridge.list_sessions()

    assert sessions
    assert sessions[0]["session_id"] == "run_20260324_bridge"
    assert sessions[0]["run_id"] == "run_bridge_1"
    assert sessions[0]["run_kind"] == "bridge_test"


@pytest.mark.asyncio
async def test_legacy_agent_bridge_creates_run_named_workspace(monkeypatch, tmp_path: Path):
    import universal_agent.api.agent_bridge as agent_bridge

    class DummyAgent:
        def __init__(self, workspace_dir: str, user_id: str, hooks):
            self.workspace_dir = workspace_dir
            self.user_id = user_id
            self.session = None

        async def initialize(self):
            return None

    class DummyHookSet:
        def __init__(self, run_id: str, active_workspace: str, enable_skills: bool):
            self.run_id = run_id
            self.active_workspace = active_workspace
            self.enable_skills = enable_skills

        def build_hooks(self):
            return {"active_workspace": self.active_workspace}

    monkeypatch.setattr(agent_bridge, "UniversalAgent", DummyAgent)
    monkeypatch.setattr(agent_bridge, "resolve_user_id", lambda: "owner_primary")
    monkeypatch.setattr("universal_agent.hooks.AgentHookSet", DummyHookSet)

    bridge = agent_bridge.AgentBridge()
    bridge.workspace_base = tmp_path
    bridge._session_roots = {tmp_path}

    info = await bridge.create_session()

    workspace = Path(info.workspace)
    assert info.session_id.startswith("run_")
    assert workspace.name == info.session_id
    assert workspace.exists()
