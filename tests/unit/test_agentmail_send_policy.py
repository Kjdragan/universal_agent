from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from universal_agent import task_hub
from universal_agent.request_runtime import RequestRuntimeContext, reset_request_runtime, set_request_runtime
from universal_agent.services.email_task_bridge import EmailTaskBridge
from universal_agent.tools.agentmail_bridge import _send_agentmail_impl


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _extract_text(result: dict) -> str:
    return str(((result.get("content") or [{}])[0]).get("text") or "")


class _DummyMailService:
    def __init__(self, result: dict[str, str]) -> None:
        self._result = result
        self.calls: list[dict[str, str]] = []

    async def send_email(self, **kwargs):
        self.calls.append({k: str(v) for k, v in kwargs.items()})
        return dict(self._result)


def _seed_email_task(db_path: Path, *, session_key: str) -> dict[str, str]:
    with _connect(db_path) as conn:
        task_hub.ensure_schema(conn)
        bridge = EmailTaskBridge(db_conn=conn)
        result = bridge.materialize(
            thread_id="thread-1",
            message_id="msg-1",
            sender_email="kevin@example.com",
            subject="Smoke test - standard report",
            reply_text="Please prepare a comprehensive report and email one final response only.",
            session_key=session_key,
            real_thread_id="thread-1",
            real_message_id="msg-1",
        )
        row = conn.execute(
            "SELECT thread_id, task_id FROM email_task_mappings WHERE thread_id = ?",
            ("thread-1",),
        ).fetchone()
    return {"thread_id": str(row["thread_id"]), "task_id": str(result["task_id"])}


@pytest.mark.asyncio
async def test_email_triage_blocks_ack_when_request_requires_single_final_response(monkeypatch, tmp_path):
    db_path = tmp_path / "activity_state.db"
    seeded = _seed_email_task(db_path, session_key="hook-1")
    service = _DummyMailService({"status": "sent", "message_id": "msg-ack"})
    monkeypatch.setattr("universal_agent.tools.agentmail_bridge._get_agentmail_service", lambda: service)
    monkeypatch.setattr("universal_agent.durable.db.get_activity_db_path", lambda: str(db_path))
    monkeypatch.setattr("universal_agent.durable.db.connect_runtime_db", lambda *_a, **_k: _connect(db_path))

    token = set_request_runtime(
        RequestRuntimeContext(
            session_id="session_hook_1",
            workspace_dir=str(tmp_path),
            source="webhook",
            run_kind="email_triage",
            user_input=(
                "Email me one final response only. "
                "Do not send multiple final reports."
            ),
            metadata={"hook_session_key": "hook-1"},
        )
    )
    try:
            result = await _send_agentmail_impl(
            {
                "to": "kevin@example.com",
                "subject": "Re: Smoke test - standard report",
                "body": "Received, Kevin. Starting research now and will respond shortly.",
            }
        )
    finally:
        reset_request_runtime(token)

    assert "requires exactly one final response" in _extract_text(result)
    assert service.calls == []
    with _connect(db_path) as conn:
        bridge = EmailTaskBridge(db_conn=conn)
        assert bridge.has_ack_outbound(seeded["thread_id"]) is False


@pytest.mark.asyncio
async def test_todo_execution_blocks_duplicate_final_outbound_after_first_draft(monkeypatch, tmp_path):
    db_path = tmp_path / "activity_state.db"
    seeded = _seed_email_task(db_path, session_key="hook-2")
    service = _DummyMailService({"status": "draft", "draft_id": "draft-1"})
    monkeypatch.setattr("universal_agent.tools.agentmail_bridge._get_agentmail_service", lambda: service)
    monkeypatch.setattr("universal_agent.durable.db.get_activity_db_path", lambda: str(db_path))
    monkeypatch.setattr("universal_agent.durable.db.connect_runtime_db", lambda *_a, **_k: _connect(db_path))

    token = set_request_runtime(
        RequestRuntimeContext(
            session_id="daemon_simone_todo",
            workspace_dir=str(tmp_path),
            source="todo_dispatcher",
            run_kind="todo_execution",
            user_input="Send exactly one final email with the report attached.",
            metadata={"claimed_task_ids": [seeded["task_id"]]},
        )
    )
    try:
            first = await _send_agentmail_impl(
            {
                "to": "kevin@example.com",
                "subject": "AI Model Releases Report",
                "body": "Kevin,\n\nHere is the executive summary and attached report.",
            }
        )
            second = await _send_agentmail_impl(
            {
                "to": "kevin@example.com",
                "subject": "AI Model Releases Report",
                "body": "Kevin,\n\nHere is the executive summary and attached report.",
            }
        )
    finally:
        reset_request_runtime(token)

    assert json.loads(_extract_text(first))["status"] == "success"
    assert "duplicate final delivery blocked" in _extract_text(second)
    assert len(service.calls) == 1
    with _connect(db_path) as conn:
        bridge = EmailTaskBridge(db_conn=conn)
        mapping = bridge.get_mapping_for_task_id(seeded["task_id"])
        assert mapping is not None
        assert str(mapping.get("final_draft_id") or "") == "draft-1"


@pytest.mark.asyncio
async def test_todo_execution_blocks_receipt_style_ack(monkeypatch, tmp_path):
    db_path = tmp_path / "activity_state.db"
    seeded = _seed_email_task(db_path, session_key="hook-3")
    service = _DummyMailService({"status": "sent", "message_id": "msg-1"})
    monkeypatch.setattr("universal_agent.tools.agentmail_bridge._get_agentmail_service", lambda: service)
    monkeypatch.setattr("universal_agent.durable.db.get_activity_db_path", lambda: str(db_path))
    monkeypatch.setattr("universal_agent.durable.db.connect_runtime_db", lambda *_a, **_k: _connect(db_path))

    token = set_request_runtime(
        RequestRuntimeContext(
            session_id="daemon_simone_todo",
            workspace_dir=str(tmp_path),
            source="todo_dispatcher",
            run_kind="todo_execution",
            user_input="Send exactly one final email with the report attached.",
            metadata={"claimed_task_ids": [seeded["task_id"]]},
        )
    )
    try:
            result = await _send_agentmail_impl(
            {
                "to": "kevin@example.com",
                "subject": "Re: Smoke test - standard report",
                "body": "Received, Kevin. Starting research now and will respond shortly.",
            }
        )
    finally:
        reset_request_runtime(token)

    assert "Receipt-style acknowledgements are not allowed" in _extract_text(result)
    assert service.calls == []
