from __future__ import annotations

import json
import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.tools import task_hub_bridge
from universal_agent.tools.task_hub_bridge import _task_hub_task_action_impl


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_tool_payload(result: dict) -> dict:
    content = result.get("content") or []
    assert content
    text = str(content[0].get("text") or "")
    assert not text.startswith("error:")
    return json.loads(text)


@pytest.mark.asyncio
async def test_task_hub_claim_alias_seizes_open_task(monkeypatch, tmp_path):
    db_path = str(tmp_path / "activity_state.db")
    with _connect(db_path) as conn:
        task_hub.ensure_schema(conn)
        task_hub.upsert_item(
            conn,
            {
                "task_id": "email:claimable",
                "title": "Email task",
                "status": task_hub.TASK_STATUS_OPEN,
            },
        )

    monkeypatch.setattr(task_hub_bridge, "get_activity_db_path", lambda: db_path)

    result = await _task_hub_task_action_impl(
        {
            "task_id": "email:claimable",
            "action": "claim",
            "agent_id": "todo:test",
        }
    )
    payload = _parse_tool_payload(result)

    assert payload["success"] is True
    assert payload["action"] == "claim"
    assert payload["normalized_action"] == "seize"
    assert payload["item"]["status"] == task_hub.TASK_STATUS_IN_PROGRESS


@pytest.mark.asyncio
async def test_task_hub_claim_alias_is_idempotent_for_in_progress_task(monkeypatch, tmp_path):
    db_path = str(tmp_path / "activity_state.db")
    with _connect(db_path) as conn:
        task_hub.ensure_schema(conn)
        task_hub.upsert_item(
            conn,
            {
                "task_id": "email:claimed",
                "title": "Email task",
                "status": task_hub.TASK_STATUS_IN_PROGRESS,
                "seizure_state": "seized",
            },
        )

    monkeypatch.setattr(task_hub_bridge, "get_activity_db_path", lambda: db_path)

    result = await _task_hub_task_action_impl(
        {
            "task_id": "email:claimed",
            "action": "claim",
            "agent_id": "todo:test",
        }
    )
    payload = _parse_tool_payload(result)

    assert payload["success"] is True
    assert payload["normalized_action"] == "seize"
    assert payload["already_claimed"] is True

    with _connect(db_path) as conn:
        assignments = conn.execute(
            "SELECT COUNT(*) AS c FROM task_hub_assignments WHERE task_id = ?",
            ("email:claimed",),
        ).fetchone()

    assert int(assignments["c"]) == 0
