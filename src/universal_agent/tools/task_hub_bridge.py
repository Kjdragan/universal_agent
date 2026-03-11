from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict

from claude_agent_sdk import tool

from universal_agent import task_hub
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

_LIFECYCLE_ACTIONS = {"review", "complete", "block", "park", "unblock"}


def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=True)}]}


def _err(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": f"error: {message}"}]}


@tool(
    name="task_hub_task_action",
    description=(
        "Perform Task Hub lifecycle actions for an existing task. "
        "Allowed actions: review, complete, block, park, unblock."
    ),
    input_schema={
        "task_id": str,
        "action": str,
        "reason": str,
        "note": str,
        "agent_id": str,
    },
)
async def task_hub_task_action_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    return await _task_hub_task_action_impl(args)


async def _task_hub_task_action_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    task_id = str(args.get("task_id", "") or "").strip()
    if not task_id:
        return _err("task_id is required")

    action = str(args.get("action", "") or "").strip().lower()
    if action not in _LIFECYCLE_ACTIONS:
        return _err(
            f"unsupported action: {action}. allowed actions: {', '.join(sorted(_LIFECYCLE_ACTIONS))}"
        )

    conn = connect_runtime_db(get_activity_db_path())
    conn.row_factory = sqlite3.Row
    try:
        updated = task_hub.perform_task_action(
            conn,
            task_id=task_id,
            action=action,
            reason=str(args.get("reason", "") or "").strip(),
            note=str(args.get("note", "") or "").strip(),
            agent_id=str(args.get("agent_id", "heartbeat_agent") or "heartbeat_agent").strip() or "heartbeat_agent",
        )
    except ValueError as exc:
        return _err(str(exc))
    finally:
        conn.close()

    return _ok(
        {
            "success": True,
            "task_id": task_id,
            "action": action,
            "item": updated,
        }
    )
