import pytest

from universal_agent.agent_core import AgentEvent, EventType
from universal_agent.durable.state import get_vp_session, list_vp_events, list_vp_missions
from universal_agent.gateway import GatewayRequest, InProcessGateway


class FakeProcessTurnAdapter:
    vp_should_error = False
    vp_raise_exception = False
    fail_vp_initialize = False
    primary_raise_exception = False

    def __init__(self, config):
        self.config = config

    async def initialize(self):
        workspace_dir = str(getattr(self.config, "workspace_dir", ""))
        if self.__class__.fail_vp_initialize and "vp_coder_primary" in workspace_dir:
            raise RuntimeError("vp adapter initialize failure")
        return None

    async def execute(self, user_input: str):
        run_source = self.config.__dict__.get("_run_source", "user")
        if run_source == "vp.coder" and self.__class__.vp_raise_exception:
            raise RuntimeError("vp hard exception")
        if run_source == "vp.coder" and self.__class__.vp_should_error:
            yield AgentEvent(type=EventType.ERROR, data={"message": "vp lane failure"})
            yield AgentEvent(type=EventType.ITERATION_END, data={"trace_id": "vp-trace"})
            return
        if run_source != "vp.coder" and self.__class__.primary_raise_exception:
            raise RuntimeError("primary path exception")

        yield AgentEvent(type=EventType.TEXT, data={"text": f"{run_source}:ok"})
        yield AgentEvent(type=EventType.ITERATION_END, data={"trace_id": f"{run_source}-trace"})

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_gateway_routes_to_coder_vp_and_persists_mission(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime_state.db"))
    monkeypatch.setenv("UA_ENABLE_CODER_VP", "1")
    monkeypatch.delenv("UA_CODER_VP_SHADOW_MODE", raising=False)
    monkeypatch.delenv("UA_CODER_VP_FORCE_FALLBACK", raising=False)

    import universal_agent.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "ProcessTurnAdapter", FakeProcessTurnAdapter)
    monkeypatch.setattr(gateway_module, "EXECUTION_ENGINE_AVAILABLE", True)
    FakeProcessTurnAdapter.vp_should_error = False
    FakeProcessTurnAdapter.vp_raise_exception = False
    FakeProcessTurnAdapter.fail_vp_initialize = False
    FakeProcessTurnAdapter.primary_raise_exception = False

    gateway = InProcessGateway(workspace_base=tmp_path / "workspaces")
    session = await gateway.create_session(user_id="owner_primary")

    request = GatewayRequest(user_input="Please fix this Python bug in the parser")
    events = [event async for event in gateway.execute(session, request)]

    assert any(
        event.type == EventType.STATUS and event.data.get("routing") == "delegated_to_coder_vp"
        for event in events
    )
    assert any(
        event.type == EventType.TEXT and event.data.get("text") == "vp.coder:ok"
        for event in events
    )

    missions = list_vp_missions(gateway._runtime_db_conn, "vp.coder.primary")
    assert len(missions) == 1
    assert missions[0]["status"] == "completed"

    mission_id = missions[0]["mission_id"]
    db_events = list_vp_events(gateway._runtime_db_conn, mission_id=mission_id)
    db_event_types = [row["event_type"] for row in db_events]
    assert "vp.mission.dispatched" in db_event_types
    assert "vp.mission.completed" in db_event_types

    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_coder_vp_error_falls_back_to_primary_path(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime_state.db"))
    monkeypatch.setenv("UA_ENABLE_CODER_VP", "1")
    monkeypatch.delenv("UA_CODER_VP_SHADOW_MODE", raising=False)
    monkeypatch.delenv("UA_CODER_VP_FORCE_FALLBACK", raising=False)

    import universal_agent.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "ProcessTurnAdapter", FakeProcessTurnAdapter)
    monkeypatch.setattr(gateway_module, "EXECUTION_ENGINE_AVAILABLE", True)
    FakeProcessTurnAdapter.vp_should_error = True
    FakeProcessTurnAdapter.vp_raise_exception = False
    FakeProcessTurnAdapter.fail_vp_initialize = False
    FakeProcessTurnAdapter.primary_raise_exception = False

    gateway = InProcessGateway(workspace_base=tmp_path / "workspaces")
    session = await gateway.create_session(user_id="owner_primary")

    request = GatewayRequest(user_input="Please implement a Python function for retries")
    events = [event async for event in gateway.execute(session, request)]

    assert any(
        event.type == EventType.STATUS and event.data.get("routing") == "coder_vp_fallback"
        for event in events
    )
    assert any(
        event.type == EventType.TEXT and event.data.get("text") == "user:ok"
        for event in events
    )

    missions = list_vp_missions(gateway._runtime_db_conn, "vp.coder.primary")
    assert len(missions) == 1
    assert missions[0]["status"] == "completed"

    mission_id = missions[0]["mission_id"]
    db_events = list_vp_events(gateway._runtime_db_conn, mission_id=mission_id)
    db_event_types = [row["event_type"] for row in db_events]
    assert "vp.mission.fallback" in db_event_types
    assert "vp.mission.completed" in db_event_types

    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_keeps_utility_prompt_on_primary_path(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime_state.db"))
    monkeypatch.setenv("UA_ENABLE_CODER_VP", "1")
    monkeypatch.delenv("UA_CODER_VP_SHADOW_MODE", raising=False)
    monkeypatch.delenv("UA_CODER_VP_FORCE_FALLBACK", raising=False)

    import universal_agent.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "ProcessTurnAdapter", FakeProcessTurnAdapter)
    monkeypatch.setattr(gateway_module, "EXECUTION_ENGINE_AVAILABLE", True)
    FakeProcessTurnAdapter.vp_should_error = False
    FakeProcessTurnAdapter.vp_raise_exception = False
    FakeProcessTurnAdapter.fail_vp_initialize = False
    FakeProcessTurnAdapter.primary_raise_exception = False

    gateway = InProcessGateway(workspace_base=tmp_path / "workspaces")
    session = await gateway.create_session(user_id="owner_primary")

    request = GatewayRequest(
        user_input="Write a bash command to recursively find .py files and print their line counts"
    )
    events = [event async for event in gateway.execute(session, request)]

    assert not any(
        event.type == EventType.STATUS and event.data.get("routing") == "delegated_to_coder_vp"
        for event in events
    )
    assert any(
        event.type == EventType.TEXT and event.data.get("text") == "user:ok"
        for event in events
    )

    missions = list_vp_missions(gateway._runtime_db_conn, "vp.coder.primary")
    assert missions == []

    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_skips_coder_vp_for_cron_source(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime_state.db"))
    monkeypatch.setenv("UA_ENABLE_CODER_VP", "1")
    monkeypatch.delenv("UA_CODER_VP_SHADOW_MODE", raising=False)
    monkeypatch.delenv("UA_CODER_VP_FORCE_FALLBACK", raising=False)

    import universal_agent.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "ProcessTurnAdapter", FakeProcessTurnAdapter)
    monkeypatch.setattr(gateway_module, "EXECUTION_ENGINE_AVAILABLE", True)
    FakeProcessTurnAdapter.vp_should_error = False
    FakeProcessTurnAdapter.vp_raise_exception = False
    FakeProcessTurnAdapter.fail_vp_initialize = False
    FakeProcessTurnAdapter.primary_raise_exception = False

    gateway = InProcessGateway(workspace_base=tmp_path / "workspaces")
    session = await gateway.create_session(user_id="cron")

    request = GatewayRequest(
        user_input="Please implement a Python module with integration tests",
        metadata={"source": "cron"},
    )
    events = [event async for event in gateway.execute(session, request)]

    assert not any(
        event.type == EventType.STATUS and event.data.get("routing") == "delegated_to_coder_vp"
        for event in events
    )
    assert any(
        event.type == EventType.TEXT and event.data.get("text") == "cron:ok"
        for event in events
    )

    missions = list_vp_missions(gateway._runtime_db_conn, "vp.coder.primary")
    assert missions == []

    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_coder_vp_bootstrap_failure_falls_back_without_mission(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime_state.db"))
    monkeypatch.setenv("UA_ENABLE_CODER_VP", "1")
    monkeypatch.delenv("UA_CODER_VP_SHADOW_MODE", raising=False)
    monkeypatch.delenv("UA_CODER_VP_FORCE_FALLBACK", raising=False)

    import universal_agent.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "ProcessTurnAdapter", FakeProcessTurnAdapter)
    monkeypatch.setattr(gateway_module, "EXECUTION_ENGINE_AVAILABLE", True)
    FakeProcessTurnAdapter.vp_should_error = False
    FakeProcessTurnAdapter.vp_raise_exception = False
    FakeProcessTurnAdapter.fail_vp_initialize = True
    FakeProcessTurnAdapter.primary_raise_exception = False

    gateway = InProcessGateway(workspace_base=tmp_path / "workspaces")
    session = await gateway.create_session(user_id="owner_primary")

    request = GatewayRequest(user_input="Please implement a Python function for retries")
    events = [event async for event in gateway.execute(session, request)]

    assert any(
        event.type == EventType.STATUS and event.data.get("routing") == "coder_vp_bootstrap_fallback"
        for event in events
    )
    assert any(
        event.type == EventType.TEXT and event.data.get("text") == "user:ok"
        for event in events
    )

    missions = list_vp_missions(gateway._runtime_db_conn, "vp.coder.primary")
    assert missions == []

    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_coder_vp_hard_exception_falls_back_to_primary_path(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime_state.db"))
    monkeypatch.setenv("UA_ENABLE_CODER_VP", "1")
    monkeypatch.delenv("UA_CODER_VP_SHADOW_MODE", raising=False)
    monkeypatch.delenv("UA_CODER_VP_FORCE_FALLBACK", raising=False)

    import universal_agent.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "ProcessTurnAdapter", FakeProcessTurnAdapter)
    monkeypatch.setattr(gateway_module, "EXECUTION_ENGINE_AVAILABLE", True)
    FakeProcessTurnAdapter.vp_should_error = False
    FakeProcessTurnAdapter.vp_raise_exception = True
    FakeProcessTurnAdapter.fail_vp_initialize = False
    FakeProcessTurnAdapter.primary_raise_exception = False

    gateway = InProcessGateway(workspace_base=tmp_path / "workspaces")
    session = await gateway.create_session(user_id="owner_primary")

    request = GatewayRequest(user_input="Please implement a Python function for retries")
    events = [event async for event in gateway.execute(session, request)]

    assert any(
        event.type == EventType.STATUS and event.data.get("routing") == "coder_vp_exception"
        for event in events
    )
    assert any(
        event.type == EventType.STATUS and event.data.get("routing") == "coder_vp_fallback"
        for event in events
    )
    assert any(
        event.type == EventType.TEXT and event.data.get("text") == "user:ok"
        for event in events
    )

    missions = list_vp_missions(gateway._runtime_db_conn, "vp.coder.primary")
    assert len(missions) == 1
    assert missions[0]["status"] == "completed"

    mission_id = missions[0]["mission_id"]
    db_events = list_vp_events(gateway._runtime_db_conn, mission_id=mission_id)
    fallback_events = [row for row in db_events if row["event_type"] == "vp.mission.fallback"]
    assert len(fallback_events) == 1
    assert "vp hard exception" in str(fallback_events[0]["payload_json"] or "")

    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_marks_vp_mission_failed_when_fallback_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime_state.db"))
    monkeypatch.setenv("UA_ENABLE_CODER_VP", "1")
    monkeypatch.delenv("UA_CODER_VP_SHADOW_MODE", raising=False)
    monkeypatch.delenv("UA_CODER_VP_FORCE_FALLBACK", raising=False)

    import universal_agent.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "ProcessTurnAdapter", FakeProcessTurnAdapter)
    monkeypatch.setattr(gateway_module, "EXECUTION_ENGINE_AVAILABLE", True)
    FakeProcessTurnAdapter.vp_should_error = False
    FakeProcessTurnAdapter.vp_raise_exception = True
    FakeProcessTurnAdapter.fail_vp_initialize = False
    FakeProcessTurnAdapter.primary_raise_exception = True

    gateway = InProcessGateway(workspace_base=tmp_path / "workspaces")
    session = await gateway.create_session(user_id="owner_primary")

    request = GatewayRequest(user_input="Please implement a Python function for retries")
    with pytest.raises(RuntimeError, match="primary path exception"):
        _ = [event async for event in gateway.execute(session, request)]

    missions = list_vp_missions(gateway._runtime_db_conn, "vp.coder.primary")
    assert len(missions) == 1
    assert missions[0]["status"] == "failed"

    mission_id = missions[0]["mission_id"]
    db_events = list_vp_events(gateway._runtime_db_conn, mission_id=mission_id)
    db_event_types = [row["event_type"] for row in db_events]
    assert "vp.mission.fallback" in db_event_types
    assert "vp.mission.failed" in db_event_types

    FakeProcessTurnAdapter.primary_raise_exception = False
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_coder_vp_restart_recovers_session_and_continues_missions(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime_state.db"))
    monkeypatch.setenv("UA_ENABLE_CODER_VP", "1")
    monkeypatch.delenv("UA_CODER_VP_SHADOW_MODE", raising=False)
    monkeypatch.delenv("UA_CODER_VP_FORCE_FALLBACK", raising=False)

    import universal_agent.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "ProcessTurnAdapter", FakeProcessTurnAdapter)
    monkeypatch.setattr(gateway_module, "EXECUTION_ENGINE_AVAILABLE", True)
    FakeProcessTurnAdapter.vp_should_error = False
    FakeProcessTurnAdapter.vp_raise_exception = False
    FakeProcessTurnAdapter.fail_vp_initialize = False
    FakeProcessTurnAdapter.primary_raise_exception = False

    gateway_a = InProcessGateway(workspace_base=tmp_path / "workspaces")
    session_a = await gateway_a.create_session(user_id="owner_primary")
    req = GatewayRequest(user_input="Implement robust retry logic in Python")
    events_a = [event async for event in gateway_a.execute(session_a, req)]
    assert any(
        event.type == EventType.STATUS and event.data.get("routing") == "delegated_to_coder_vp"
        for event in events_a
    )
    await gateway_a.close()

    gateway_b = InProcessGateway(workspace_base=tmp_path / "workspaces")
    session_b = await gateway_b.create_session(user_id="owner_primary")
    events_b = [event async for event in gateway_b.execute(session_b, req)]
    assert any(
        event.type == EventType.STATUS and event.data.get("routing") == "delegated_to_coder_vp"
        for event in events_b
    )

    missions = list_vp_missions(gateway_b._runtime_db_conn, "vp.coder.primary")
    assert len(missions) == 2
    assert all(row["status"] == "completed" for row in missions)

    session_row = get_vp_session(gateway_b._runtime_db_conn, "vp.coder.primary")
    assert session_row is not None
    assert session_row["lease_owner"] == "simone-control-plane"
    assert session_row["status"] == "active"

    await gateway_b.close()
