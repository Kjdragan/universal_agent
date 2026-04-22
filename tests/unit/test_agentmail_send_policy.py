from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from universal_agent import task_hub
from universal_agent.request_runtime import RequestRuntimeContext, reset_request_runtime, set_request_runtime
from universal_agent.services.email_task_bridge import EmailTaskBridge, reconcile_terminal_email_task_mappings
from universal_agent.tools.agentmail_bridge import _send_agentmail_impl
from universal_agent.tools.local_toolkit_bridge import _agentmail_send_with_local_attachments_impl


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


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, str]) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


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


def _seed_chat_task(db_path: Path, *, task_id: str) -> None:
    with _connect(db_path) as conn:
        task_hub.ensure_schema(conn)
        task_hub.upsert_item(
            conn,
            {
                "task_id": task_id,
                "source_kind": "chat_panel",
                "source_ref": "session_chat_repeat",
                "title": "Email me a report",
                "description": "Create a report and email it to me.",
                "project_key": "immediate",
                "priority": 1,
                "labels": ["chat-panel", "interactive"],
                "status": task_hub.TASK_STATUS_IN_PROGRESS,
                "agent_ready": True,
                "seizure_state": "seized",
                "metadata": {
                    "delivery_mode": "standard_report",
                    "dispatch": {
                        "active_assignment_id": "asg-chat-1",
                        "active_provider_session_id": "session_chat_repeat",
                    },
                },
            },
        )
        conn.execute(
            """
            INSERT INTO task_hub_assignments (
                assignment_id, task_id, agent_id, provider_session_id, state, started_at
            ) VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            ("asg-chat-1", task_id, "todo:session_chat_repeat", "session_chat_repeat", "running"),
        )
        conn.commit()


def test_record_final_outbound_stamps_delivery_even_without_provider_ids(tmp_path):
    db_path = tmp_path / "activity_state.db"
    seeded = _seed_email_task(db_path, session_key="hook-record-final")

    with _connect(db_path) as conn:
        bridge = EmailTaskBridge(db_conn=conn)
        bridge.record_final_outbound(seeded["thread_id"])
        row = conn.execute(
            """
            SELECT email_sent_at, final_email_sent_at, final_message_id, final_draft_id
            FROM email_task_mappings
            WHERE thread_id = ?
            """,
            (seeded["thread_id"],),
        ).fetchone()

    assert str(row["email_sent_at"] or "")
    assert str(row["final_email_sent_at"] or "")
    assert str(row["final_message_id"] or "") == ""
    assert str(row["final_draft_id"] or "") == ""


def test_target_agent_labels_use_exact_map(tmp_path):
    db_path = tmp_path / "activity_state.db"
    with _connect(db_path) as conn:
        task_hub.ensure_schema(conn)
        bridge = EmailTaskBridge(db_conn=conn)
        coder = bridge.materialize(
            thread_id="thread-coder",
            message_id="msg-coder",
            sender_email="kevin@example.com",
            subject="For Codie",
            reply_text="Please handle this.",
            target_agent="vp.coder.primary",
        )
        general = bridge.materialize(
            thread_id="thread-general",
            message_id="msg-general",
            sender_email="kevin@example.com",
            subject="For Atlas",
            reply_text="Please handle this.",
            target_agent="vp.general.primary",
        )
        unknown = bridge.materialize(
            thread_id="thread-unknown",
            message_id="msg-unknown",
            sender_email="kevin@example.com",
            subject="For unknown",
            reply_text="Please handle this.",
            target_agent="vp.encoder.primary",
        )
        coder_item = task_hub.get_item(conn, coder["task_id"])
        general_item = task_hub.get_item(conn, general["task_id"])
        unknown_item = task_hub.get_item(conn, unknown["task_id"])

    assert "agent-codie" in (coder_item or {}).get("labels", [])
    assert "agent-atlas" in (general_item or {}).get("labels", [])
    assert "agent-atlas" in (unknown_item or {}).get("labels", [])
    assert "agent-codie" not in (unknown_item or {}).get("labels", [])


def _seed_report_task(db_path: Path, *, task_id: str) -> None:
    with _connect(db_path) as conn:
        task_hub.ensure_schema(conn)
        task_hub.upsert_item(
            conn,
            {
                "task_id": task_id,
                "source_kind": "claude_code_demo_task",
                "source_ref": "post-1",
                "title": "Send report",
                "description": "Generate and email a report with attachments.",
                "project_key": "proactive",
                "priority": 3,
                "labels": ["agent-ready"],
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
                "metadata": {
                    "workflow_manifest": {
                        "workflow_kind": "research_report_email",
                        "delivery_mode": "standard_report",
                        "final_channel": "email",
                    }
                },
            },
        )
        task_hub.claim_next_dispatch_tasks(
            conn,
            limit=1,
            agent_id="todo:daemon_simone_todo",
            workflow_run_id="run-attachment-email",
            provider_session_id="daemon_simone_todo",
        )


@pytest.mark.asyncio
async def test_local_attachment_wrapper_records_delivery_for_claimed_task(monkeypatch, tmp_path):
    db_path = tmp_path / "activity_state.db"
    _seed_report_task(db_path, task_id="task:attachment-email")
    attachment = tmp_path / "report.md"
    attachment.write_text("# report\n", encoding="utf-8")

    monkeypatch.setenv("AGENTMAIL_API_KEY", "test-key")
    monkeypatch.setattr("universal_agent.durable.db.get_activity_db_path", lambda: str(db_path))
    monkeypatch.setattr("universal_agent.durable.db.connect_runtime_db", lambda *_a, **_k: _connect(db_path))
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, data=None, timeout=0: _FakeHTTPResponse({"messageId": "msg-attach", "threadId": "thread-attach"}),
    )

    token = set_request_runtime(
        RequestRuntimeContext(
            session_id="daemon_simone_todo",
            workspace_dir=str(tmp_path),
            source="todo_dispatcher",
            run_kind="todo_execution",
            metadata={"claimed_task_ids": ["task:attachment-email"]},
        )
    )
    try:
        result = await _agentmail_send_with_local_attachments_impl(
            {
                "inboxId": "oddcity216@agentmail.to",
                "to": ["kevin@example.com"],
                "subject": "Attached report",
                "text": "See attached.",
                "attachment_paths": [str(attachment)],
            }
        )
    finally:
        reset_request_runtime(token)

    assert "Email successfully sent" in _extract_text(result)
    with _connect(db_path) as conn:
        item = task_hub.get_item(conn, "task:attachment-email")
        outbound = (((item or {}).get("metadata") or {}).get("dispatch") or {}).get("outbound_delivery") or {}
        assert outbound["message_id"] == "msg-attach"
        assert outbound["channel"] == "agentmail"


def test_attachment_delivery_allows_complete_instead_of_review(tmp_path):
    db_path = tmp_path / "activity_state.db"
    _seed_report_task(db_path, task_id="task:attachment-complete")
    with _connect(db_path) as conn:
        task_hub.record_task_outbound_delivery(
            conn,
            task_id="task:attachment-complete",
            channel="agentmail",
            message_id="msg-attach-complete",
        )
        updated = task_hub.perform_task_action(
            conn,
            task_id="task:attachment-complete",
            action="complete",
            reason="Sent the report with attachments via AgentMail",
            agent_id="todo:daemon_simone_todo",
        )

    assert updated["status"] == task_hub.TASK_STATUS_COMPLETED
    dispatch = ((updated.get("metadata") or {}).get("dispatch") or {})
    assert dispatch["completion_unverified"] is False


def test_reconcile_terminal_email_task_mappings_backfills_completed_rows(tmp_path):
    db_path = tmp_path / "activity_state.db"
    seeded = _seed_email_task(db_path, session_key="hook-reconcile")

    with _connect(db_path) as conn:
        task_hub.record_task_outbound_delivery(
            conn,
            task_id=seeded["task_id"],
            channel="agentmail",
            message_id="msg-unit-test",
        )
        task_hub.perform_task_action(
            conn,
            task_id=seeded["task_id"],
            action="complete",
            reason="unit-test",
            agent_id="unit-test",
        )
        result = reconcile_terminal_email_task_mappings(conn)
        row = conn.execute(
            """
            SELECT status, email_sent_at, final_email_sent_at
            FROM email_task_mappings
            WHERE thread_id = ?
            """,
            (seeded["thread_id"],),
        ).fetchone()

    assert result["completed_backfilled"] == 1
    assert result["completed_status_synced"] == 1
    assert str(row["status"] or "") == "completed"
    assert str(row["email_sent_at"] or "")
    assert str(row["final_email_sent_at"] or "")


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


@pytest.mark.asyncio
async def test_todo_execution_blocks_duplicate_final_outbound_for_chat_task(monkeypatch, tmp_path):
    db_path = tmp_path / "activity_state.db"
    task_id = "chat:session_chat_repeat:turn_001"
    _seed_chat_task(db_path, task_id=task_id)
    service = _DummyMailService({"status": "sent", "message_id": "msg-chat-1"})
    monkeypatch.setattr("universal_agent.tools.agentmail_bridge._get_agentmail_service", lambda: service)
    monkeypatch.setattr("universal_agent.durable.db.get_activity_db_path", lambda: str(db_path))
    monkeypatch.setattr("universal_agent.durable.db.connect_runtime_db", lambda *_a, **_k: _connect(db_path))

    token = set_request_runtime(
        RequestRuntimeContext(
            session_id="session_chat_repeat",
            workspace_dir=str(tmp_path),
            source="chat_panel_task_hub",
            run_kind="todo_execution",
            user_input="Create a full report on topic Y and email it to me.",
            metadata={"claimed_task_ids": [task_id]},
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
        item = task_hub.get_item(conn, task_id)
        outbound = (((item or {}).get("metadata") or {}).get("dispatch") or {}).get("outbound_delivery") or {}
        assert str(outbound.get("message_id") or "") == "msg-chat-1"
