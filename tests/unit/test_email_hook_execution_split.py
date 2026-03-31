from __future__ import annotations

import sqlite3
from pathlib import Path

from universal_agent import task_hub
from universal_agent.hooks_service import HookAction, HooksService


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_email_handler_prompt_is_triage_only():
    service = HooksService.__new__(HooksService)
    action = HookAction(
        kind="agent",
        name="AgentMailInbound",
        session_key="agentmail_thread_1",
        to="email-handler",
        message="Please research this and email me the final report.",
    )

    prompt = service._build_email_handler_prompt(action, action.message or "")

    assert "TRIAGE-ONLY" in prompt
    assert "receipt-only" in prompt
    assert "do not run research" in prompt.lower()
    assert "vp_dispatch_mission" not in prompt
    assert "task_hub_task_action(action='delegate'" not in prompt


def test_email_hook_triage_records_metadata_without_completing_task(monkeypatch, tmp_path):
    db_path = tmp_path / "activity_state.db"

    with _connect(db_path) as conn:
        task_hub.ensure_schema(conn)
        task_hub.upsert_item(
            conn,
            {
                "task_id": "email:triage",
                "title": "Email task",
                "status": task_hub.TASK_STATUS_OPEN,
                "metadata": {"session_key": "agentmail_thread_1"},
            },
        )

    monkeypatch.setattr("universal_agent.durable.db.connect_runtime_db", lambda *_a, **_k: _connect(db_path))
    monkeypatch.setattr("universal_agent.durable.db.get_activity_db_path", lambda: str(db_path))

    service = HooksService.__new__(HooksService)
    service._record_email_task_hook_triage(
        session_key="agentmail_thread_1",
        session_id="session_hook_agentmail_thread_1",
        execution_summary="triage completed",
    )

    with _connect(db_path) as conn:
        item = task_hub.get_item(conn, "email:triage")

    assert item is not None
    assert item["status"] == task_hub.TASK_STATUS_OPEN
    assert item["metadata"]["canonical_execution_owner"] == "todo_dispatcher"
    assert item["metadata"]["workflow_manifest"]["canonical_executor"] == "simone_first"
    assert item["metadata"]["hook_triage"]["session_id"] == "session_hook_agentmail_thread_1"


def test_email_hook_triage_coerces_non_string_summary(monkeypatch, tmp_path):
    db_path = tmp_path / "activity_state.db"

    with _connect(db_path) as conn:
        task_hub.ensure_schema(conn)
        task_hub.upsert_item(
            conn,
            {
                "task_id": "email:triage-dict",
                "title": "Email task",
                "status": task_hub.TASK_STATUS_OPEN,
                "metadata": {"session_key": "agentmail_thread_dict"},
            },
        )

    monkeypatch.setattr("universal_agent.durable.db.connect_runtime_db", lambda *_a, **_k: _connect(db_path))
    monkeypatch.setattr("universal_agent.durable.db.get_activity_db_path", lambda: str(db_path))

    service = HooksService.__new__(HooksService)
    service._record_email_task_hook_triage(
        session_key="agentmail_thread_dict",
        session_id="session_hook_agentmail_thread_dict",
        execution_summary={"decision": "accepted", "notes": ["triaged"]},
    )

    with _connect(db_path) as conn:
        item = task_hub.get_item(conn, "email:triage-dict")

    assert item is not None
    assert item["metadata"]["hook_triage"]["session_id"] == "session_hook_agentmail_thread_dict"
    summary = str(item["metadata"]["hook_triage"]["summary"] or "")
    assert "decision" in summary
    assert "accepted" in summary
