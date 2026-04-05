from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from universal_agent import task_hub
from universal_agent.hooks import AgentHookSet
from universal_agent.request_runtime import (
    RequestRuntimeContext,
    reset_request_runtime,
    set_request_runtime,
)
from universal_agent.services.email_task_bridge import EmailTaskBridge


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


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
async def test_official_agentmail_pretool_blocks_receipt_ack_for_single_final(monkeypatch, tmp_path):
    db_path = tmp_path / "activity_state.db"
    seeded = _seed_email_task(db_path, session_key="hook-official-ack")

    monkeypatch.setattr("universal_agent.durable.db.get_activity_db_path", lambda: str(db_path))
    monkeypatch.setattr("universal_agent.durable.db.connect_runtime_db", lambda *_a, **_k: _connect(db_path))

    token = set_request_runtime(
        RequestRuntimeContext(
            session_id="session_hook_agentmail_1",
            workspace_dir=str(tmp_path),
            source="webhook",
            run_kind="email_triage",
            user_input="Email me one final response only. Do not send multiple reports.",
            metadata={"hook_session_key": "hook-official-ack"},
        )
    )
    try:
        hooks = AgentHookSet(run_id="unit-agentmail-official", active_workspace=str(tmp_path))
        result = await hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "mcp__agentmail__send_message",
                "tool_input": {
                    "to": ["kevin@example.com"],
                    "subject": "Re: Smoke test - standard report",
                    "text": "Received, Kevin. Starting now and will respond shortly.",
                },
            },
            "tool-official-1",
            {},
        )
    finally:
        reset_request_runtime(token)

    assert result["decision"] == "block"
    assert "exactly one final response" in result["systemMessage"]
    with _connect(db_path) as conn:
        bridge = EmailTaskBridge(db_conn=conn)
        assert bridge.has_ack_outbound(seeded["thread_id"]) is False


@pytest.mark.asyncio
async def test_official_agentmail_posttool_records_final_delivery(monkeypatch, tmp_path):
    db_path = tmp_path / "activity_state.db"
    seeded = _seed_email_task(db_path, session_key="hook-official-final")

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
        hooks = AgentHookSet(run_id="unit-agentmail-official-final", active_workspace=str(tmp_path))
        await hooks.on_post_email_send_artifact(
            {
                "tool_name": "mcp__agentmail__send_message",
                "tool_input": {
                    "to": ["kevin@example.com"],
                    "subject": "AI Model Releases Report",
                    "text": "Kevin,\n\nHere is the report.",
                },
                "tool_result": {"status": "sent", "message_id": "msg-final-1"},
                "is_error": False,
            },
            "tool-official-2",
            {},
        )
    finally:
        reset_request_runtime(token)

    with _connect(db_path) as conn:
        bridge = EmailTaskBridge(db_conn=conn)
        mapping = bridge.get_mapping_for_task_id(seeded["task_id"])
        item = task_hub.get_item(conn, seeded["task_id"])

    assert mapping is not None
    assert str(mapping.get("final_message_id") or "") == "msg-final-1"
    outbound = (((item or {}).get("metadata") or {}).get("dispatch") or {}).get("outbound_delivery") or {}
    assert str(outbound.get("message_id") or "") == "msg-final-1"
