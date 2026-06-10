"""P6 guards: tutorial_build demo lane runs the real /goal build loop on ZAI.

Pins the three dispatch-side links of the chain
(15_demo_tutorial_pipeline_adr.md § P6):

1. The producer stamps ``metadata.use_goal_loop=True`` on every
   ``tutorial_build`` Task Hub row (``queue_tutorial_build_task``) — the
   flag-independent per-task override path in
   ``services/self_briefing.is_goal_eligible_mission``.
2. That stamp makes the mission /goal-eligible on ZAI WITHOUT the global
   ``UA_VP_GOAL_ENABLED`` flag.
3. ``_vp_dispatch_mission_impl`` forces ``execution_mode="cli"`` for
   goal-eligible missions even when ``cody_mode="zai"`` (the /goal harness
   only exists in the spawned ``claude`` CLI).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
from typing import Any

from universal_agent import task_hub
from universal_agent.durable.db import connect_runtime_db, get_vp_db_path
from universal_agent.services.proactive_tutorial_builds import (
    queue_tutorial_build_task,
)
from universal_agent.services.self_briefing import is_goal_eligible_mission
from universal_agent.tools.vp_orchestration import _vp_dispatch_mission_impl


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _unwrap(result: dict) -> dict:
    assert "content" in result
    return json.loads(result["content"][0]["text"])


# ── 1. Producer stamps the flag ─────────────────────────────────────────────

def test_queue_tutorial_build_task_stamps_use_goal_loop(tmp_path):
    with _connect(tmp_path / "activity.db") as conn:
        result = queue_tutorial_build_task(
            conn,
            video_id="goalflag1",
            video_title="Build an MCP server",
            video_url="https://youtube.test/watch?v=goalflag1",
            channel_name="AI Builder",
            extraction_plan={"language": "python"},
        )
        task = task_hub.get_item(conn, result["task"]["task_id"])

    assert task is not None
    assert task["metadata"]["use_goal_loop"] is True


# ── 2. Eligibility on ZAI without the global flag ───────────────────────────

def test_tutorial_build_mission_is_goal_eligible_on_zai_without_global_flag(monkeypatch):
    monkeypatch.delenv("UA_VP_GOAL_ENABLED", raising=False)
    mission = {
        "vp_id": "vp.coder.primary",
        "payload_json": json.dumps(
            {"metadata": {"use_goal_loop": True, "cody_mode": "zai"}}
        ),
    }
    assert is_goal_eligible_mission(mission) is True


# ── 3. Dispatch forces execution_mode=cli for goal-eligible ZAI missions ────

def _read_mission_payload(mission_id: str) -> dict[str, Any]:
    conn = connect_runtime_db(get_vp_db_path())
    try:
        row = conn.execute(
            "SELECT payload_json FROM vp_missions WHERE mission_id = ?",
            (mission_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return json.loads(row["payload_json"] or "{}")


def test_dispatch_forces_cli_for_goal_eligible_zai_mission(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_VP_DB_PATH", str((tmp_path / "vp_state.db").resolve()))
    monkeypatch.setenv(
        "UA_ACTIVITY_DB_PATH", str((tmp_path / "activity_state.db").resolve())
    )
    monkeypatch.delenv("UA_VP_GOAL_ENABLED", raising=False)

    # Full producer chain: queue_tutorial_build_task stamps use_goal_loop=True.
    activity_conn = connect_runtime_db(str((tmp_path / "activity_state.db").resolve()))
    try:
        queued = queue_tutorial_build_task(
            activity_conn,
            video_id="goalcli1",
            video_title="Build agents with the Agent SDK",
            video_url="https://youtube.test/watch?v=goalcli1",
            channel_name="AI Builder",
            extraction_plan={"language": "python"},
        )
        task_id = queued["task"]["task_id"]
    finally:
        activity_conn.close()

    dispatched = _unwrap(
        asyncio.run(
            _vp_dispatch_mission_impl(
                {
                    "vp_id": "vp.coder.primary",
                    "objective": "Build the tutorial demo",
                    "mission_type": "task",
                    "cody_mode": "zai",
                    "task_id": task_id,
                    "idempotency_key": "goal-cli-1",
                }
            )
        )
    )
    assert dispatched["ok"] is True

    payload = _read_mission_payload(dispatched["mission_id"])
    assert payload.get("execution_mode") == "cli"
    assert payload.get("metadata", {}).get("use_goal_loop") is True
    assert payload.get("metadata", {}).get("cody_mode") == "zai"


def test_dispatch_keeps_sdk_for_zai_mission_without_goal_flag(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_VP_DB_PATH", str((tmp_path / "vp_state.db").resolve()))
    monkeypatch.setenv(
        "UA_ACTIVITY_DB_PATH", str((tmp_path / "activity_state.db").resolve())
    )
    monkeypatch.delenv("UA_VP_GOAL_ENABLED", raising=False)

    # Linked task WITHOUT the use_goal_loop stamp.
    activity_conn = connect_runtime_db(str((tmp_path / "activity_state.db").resolve()))
    try:
        task_hub.ensure_schema(activity_conn)
        task_hub.upsert_item(
            activity_conn,
            {
                "task_id": "plain-task-1",
                "title": "Plain coding task",
                "source_kind": "operator_dispatched",
                "metadata": {},
            },
        )
        activity_conn.commit()
    finally:
        activity_conn.close()

    dispatched = _unwrap(
        asyncio.run(
            _vp_dispatch_mission_impl(
                {
                    "vp_id": "vp.coder.primary",
                    "objective": "Do a plain coding thing",
                    "mission_type": "task",
                    "cody_mode": "zai",
                    "task_id": "plain-task-1",
                    "idempotency_key": "goal-cli-2",
                }
            )
        )
    )
    assert dispatched["ok"] is True

    payload = _read_mission_payload(dispatched["mission_id"])
    assert payload.get("execution_mode") == "sdk"
    assert "use_goal_loop" not in payload.get("metadata", {})
