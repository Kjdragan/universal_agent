import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from universal_agent import gateway_server


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def test_turn_lineage_file_tracks_start_and_finalize(tmp_path, monkeypatch):
    session_id = "session_lineage"
    turn_id = "turn_demo"
    workspace = tmp_path / session_id
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "run.log").write_text("seed\n", encoding="utf-8")

    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_session_turn_state", {})
    session = SimpleNamespace(session_id=session_id, workspace_dir=str(workspace), metadata={})
    gateway_server._sessions[session_id] = session

    admitted = gateway_server._admit_turn(
        session_id=session_id,
        connection_id="conn-1",
        user_input="send interim and final updates",
        force_complex=True,
        metadata={"source": "user"},
        client_turn_id=turn_id,
    )
    assert admitted["decision"] == "accepted"
    assert admitted["turn_id"] == turn_id

    gateway_server._finalize_turn(
        session_id,
        turn_id,
        gateway_server.TURN_STATUS_COMPLETED,
        completion={"tool_calls": 2, "duration_seconds": 1.23},
    )

    lineage_path = workspace / gateway_server.TURN_LINEAGE_DIRNAME / f"{turn_id}.jsonl"
    assert lineage_path.exists()
    rows = _read_jsonl(lineage_path)
    assert len(rows) == 2
    assert rows[0]["event"] == "turn_started"
    assert rows[1]["event"] == "turn_finalized"
    assert rows[1]["status"] == gateway_server.TURN_STATUS_COMPLETED
    assert rows[1]["run_log_offset_end"] >= rows[0]["run_log_offset_start"]


def test_runtime_foreground_counters_are_separate_from_heartbeat(monkeypatch):
    session_id = "session_runtime"
    monkeypatch.setattr(gateway_server, "_session_runtime", {})

    gateway_server._increment_session_active_runs(session_id, run_source="heartbeat")
    runtime = gateway_server._session_runtime_snapshot(session_id)
    assert runtime["active_runs"] == 1
    assert runtime["active_foreground_runs"] == 0

    gateway_server._increment_session_active_runs(session_id, run_source="user")
    runtime = gateway_server._session_runtime_snapshot(session_id)
    assert runtime["active_runs"] == 2
    assert runtime["active_foreground_runs"] == 1

    gateway_server._decrement_session_active_runs(session_id, run_source="heartbeat")
    runtime = gateway_server._session_runtime_snapshot(session_id)
    assert runtime["active_runs"] == 1
    assert runtime["active_foreground_runs"] == 1

    gateway_server._decrement_session_active_runs(session_id, run_source="user")
    runtime = gateway_server._session_runtime_snapshot(session_id)
    assert runtime["active_runs"] == 0
    assert runtime["active_foreground_runs"] == 0


@pytest.mark.asyncio
async def test_finish_session_run_auto_closes_idle_webhook_session(monkeypatch, tmp_path):
    session_id = "session_hook_auto"
    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_session_runtime", {})
    monkeypatch.setattr(gateway_server, "_pending_gated_requests", {session_id: {"gate": True}})

    session = SimpleNamespace(
        session_id=session_id,
        workspace_dir=str(tmp_path / session_id),
        metadata={"source": "webhook"},
    )
    gateway_server._sessions[session_id] = session
    runtime = gateway_server._session_runtime_snapshot(session_id)
    runtime["active_runs"] = 1
    runtime["active_connections"] = 0

    closed: list[str] = []

    class _GatewayStub:
        _ADMIN_SOURCES = frozenset({"cron", "heartbeat", "hooks", "webhook", "ops", "system"})

        async def close_session(self, sid: str) -> None:
            closed.append(sid)

    monkeypatch.setattr(gateway_server, "get_gateway", lambda: _GatewayStub())

    gateway_server._finish_session_run(
        session_id,
        run_source="webhook",
        terminal_reason="completed",
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    runtime = gateway_server._session_runtime_snapshot(session_id)
    assert runtime["lifecycle_state"] == gateway_server.SESSION_STATE_TERMINAL
    assert runtime["terminal_reason"] == "completed"
    assert session_id not in gateway_server._pending_gated_requests
    assert closed == [session_id]


@pytest.mark.asyncio
async def test_finish_session_run_keeps_user_session_open_after_background_heartbeat(monkeypatch, tmp_path):
    session_id = "session_user_bg"
    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_session_runtime", {})
    monkeypatch.setattr(gateway_server, "_pending_gated_requests", {})

    session = SimpleNamespace(
        session_id=session_id,
        workspace_dir=str(tmp_path / session_id),
        metadata={"source": "user"},
    )
    gateway_server._sessions[session_id] = session
    runtime = gateway_server._session_runtime_snapshot(session_id)
    runtime["active_runs"] = 1
    runtime["active_connections"] = 0

    closed: list[str] = []

    class _GatewayStub:
        _ADMIN_SOURCES = frozenset({"cron", "heartbeat", "hooks", "webhook", "ops", "system"})

        async def close_session(self, sid: str) -> None:
            closed.append(sid)

    monkeypatch.setattr(gateway_server, "get_gateway", lambda: _GatewayStub())

    gateway_server._finish_session_run(
        session_id,
        run_source="heartbeat",
        terminal_reason="completed",
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    runtime = gateway_server._session_runtime_snapshot(session_id)
    assert runtime["active_runs"] == 0
    assert runtime["lifecycle_state"] == gateway_server.SESSION_STATE_IDLE
    assert runtime["terminal_reason"] is None
    assert closed == []


@pytest.mark.asyncio
async def test_finish_session_run_keeps_automation_session_open_with_active_connections(
    monkeypatch, tmp_path
):
    session_id = "session_webhook_connected"
    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_session_runtime", {})
    monkeypatch.setattr(gateway_server, "_pending_gated_requests", {})

    session = SimpleNamespace(
        session_id=session_id,
        workspace_dir=str(tmp_path / session_id),
        metadata={"source": "webhook"},
    )
    gateway_server._sessions[session_id] = session
    runtime = gateway_server._session_runtime_snapshot(session_id)
    runtime["active_runs"] = 1
    runtime["active_connections"] = 1

    closed: list[str] = []

    class _GatewayStub:
        _ADMIN_SOURCES = frozenset({"cron", "heartbeat", "hooks", "webhook", "ops", "system"})

        async def close_session(self, sid: str) -> None:
            closed.append(sid)

    monkeypatch.setattr(gateway_server, "get_gateway", lambda: _GatewayStub())

    gateway_server._finish_session_run(
        session_id,
        run_source="webhook",
        terminal_reason="completed",
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    runtime = gateway_server._session_runtime_snapshot(session_id)
    assert runtime["active_runs"] == 0
    assert runtime["active_connections"] == 1
    assert runtime["lifecycle_state"] == gateway_server.SESSION_STATE_IDLE
    assert runtime["terminal_reason"] is None
    assert closed == []
