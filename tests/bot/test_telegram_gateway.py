import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from universal_agent.bot.agent_adapter import AgentAdapter, AgentRequest
from universal_agent.bot.task_manager import Task
from universal_agent.gateway import GatewayResult, GatewaySession
from universal_agent.session_checkpoint import SessionCheckpoint


@pytest.mark.asyncio
async def test_get_or_create_session_uses_telegram_prefix_and_workspace():
    adapter = AgentAdapter()
    adapter.initialized = True
    adapter.gateway = AsyncMock()
    adapter.gateway.create_session.return_value = GatewaySession(
        session_id="session_abc123",
        user_id="telegram_12345",
        workspace_dir="/tmp/ws",
    )

    session = await adapter._get_or_create_session("12345")

    adapter.gateway.create_session.assert_awaited_once_with(
        user_id="telegram_12345",
        workspace_dir=os.path.join("AGENT_RUN_WORKSPACES", "tg_12345"),
    )
    assert session.session_id == "session_abc123"


@pytest.mark.asyncio
async def test_get_or_create_session_continuation_prefers_resume():
    adapter = AgentAdapter()
    adapter.initialized = True
    adapter.gateway = AsyncMock()
    adapter.gateway.resume_session.return_value = GatewaySession(
        session_id="tg_12345",
        user_id="telegram_12345",
        workspace_dir="/tmp/ws",
    )

    session = await adapter._get_or_create_session("12345", continue_session=True)

    adapter.gateway.resume_session.assert_awaited_once_with("tg_12345")
    adapter.gateway.create_session.assert_not_called()
    assert session.session_id == "tg_12345"


@pytest.mark.asyncio
async def test_get_or_create_session_injects_checkpoint_when_present(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    user_id = "u_checkpoint"
    workspace_dir = Path("AGENT_RUN_WORKSPACES") / f"tg_{user_id}"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    adapter = AgentAdapter()
    adapter.initialized = True
    adapter.gateway = AsyncMock()
    adapter.gateway.create_session.return_value = GatewaySession(
        session_id="session_ckpt",
        user_id=f"telegram_{user_id}",
        workspace_dir=str(workspace_dir),
    )

    checkpoint = SessionCheckpoint(
        session_id="prior_session",
        timestamp="2026-02-18T00:00:00Z",
        original_request="Prior request",
        completed_tasks=["Output A", "Follow up B"],
        artifacts=[{"path": "work_products/report.md", "description": "Report output"}],
    )

    monkeypatch.setattr(
        "universal_agent.bot.agent_adapter.SessionCheckpointGenerator.load_latest",
        lambda self: checkpoint,
    )

    session = await adapter._get_or_create_session(user_id)

    assert hasattr(session, "_injected_context")
    assert "Prior request" in session._injected_context
    assert "Follow up B" in session._injected_context


@pytest.mark.asyncio
async def test_client_actor_loop_processes_request_and_returns_result():
    adapter = AgentAdapter()
    adapter.initialized = True
    adapter.gateway = AsyncMock()
    adapter.gateway.create_session.return_value = GatewaySession(
        session_id="session_actor",
        user_id="telegram_user1",
        workspace_dir="/tmp/ws_actor",
    )
    adapter.gateway.run_query.return_value = GatewayResult(
        response_text="Hello from gateway",
        tool_calls=1,
        trace_id="trace_123",
    )

    adapter._shutdown_event.clear()
    adapter.worker_task = asyncio.create_task(adapter._client_actor_loop())

    reply_future = asyncio.get_running_loop().create_future()
    req = AgentRequest(
        prompt="Hello",
        user_id="user1",
        workspace_dir=None,
        continue_session=False,
        reply_future=reply_future,
    )

    await adapter.request_queue.put(req)
    result = await asyncio.wait_for(reply_future, timeout=2.0)

    assert result.response_text == "Hello from gateway"
    assert result.trace_id == "trace_123"
    adapter.gateway.create_session.assert_awaited()
    adapter.gateway.run_query.assert_awaited()

    await adapter.shutdown()


@pytest.mark.asyncio
async def test_execute_honors_telegram_timeout_env(monkeypatch):
    adapter = AgentAdapter()
    adapter.initialized = True
    adapter.gateway = AsyncMock()
    task = Task(user_id=1001, prompt="long running")

    monkeypatch.setenv("UA_TELEGRAM_TASK_TIMEOUT_SECONDS", "0.01")

    await adapter.execute(task)

    assert task.status == "error"
    assert "timed out" in str(task.result).lower()
