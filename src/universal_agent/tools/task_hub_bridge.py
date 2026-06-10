from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict

from claude_agent_sdk import tool

from universal_agent import task_hub
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.feature_flags import task_hub_missions_enabled

_ACTION_ALIASES = {
    "claim": "seize",
}
_LIFECYCLE_ACTIONS = {"review", "complete", "block", "park", "unblock", "delegate", "approve", "seize", "claim"}


def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=True)}]}


def _err(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": f"error: {message}"}]}


@tool(
    name="task_hub_task_action",
    description=(
        "Perform Task Hub lifecycle actions for an existing task. "
        "Allowed actions: claim, seize, review, complete, block, park, unblock, delegate, approve. "
        "For delegate: set reason=<vp_id> (e.g. 'vp.general.primary') and note='mission_id=<id>'. "
        "For approve: marks a VP-completed pending_review task as completed with sign-off."
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
    action_norm = _ACTION_ALIASES.get(action, action)

    conn = connect_runtime_db(get_activity_db_path())
    conn.row_factory = sqlite3.Row
    try:
        item = task_hub.get_item(conn, task_id)
        if not item:
            return _err(f"No task found with ID: {task_id}")

        # ToDo execution tasks are already claimed by the dispatcher. If the
        # model retries a claim, treat it as a no-op instead of creating a
        # duplicate assignment or sending it into a retry loop.
        if action_norm == "seize" and str(item.get("status") or "").strip().lower() == task_hub.TASK_STATUS_IN_PROGRESS:
            return _ok(
                {
                    "success": True,
                    "task_id": task_id,
                    "action": action,
                    "normalized_action": action_norm,
                    "already_claimed": True,
                    "item": item,
                }
            )

        updated = task_hub.perform_task_action(
            conn,
            task_id=task_id,
            action=action_norm,
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
            "normalized_action": action_norm,
            "item": updated,
        }
    )


# ── Phase 2: Task Decomposition Tool ─────────────────────────────────────────


@tool(
    name="task_hub_decompose",
    description=(
        "Decompose a multi-part task into linked sub-tasks. "
        "The parent task is marked as 'decomposed' and each sub-task "
        "is created with a parent_task_id link. Use when a single task "
        "contains multiple distinct work items that should be tracked "
        "and potentially delegated independently. "
        "subtasks: JSON array of objects, each with at minimum 'title' (string). "
        "Optional per sub-task: description, priority (0-3), labels (array)."
    ),
    input_schema={
        "parent_task_id": str,
        "subtasks": str,  # JSON-encoded array
    },
)
async def task_hub_decompose_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    return await _task_hub_decompose_impl(args)


async def _task_hub_decompose_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    parent_task_id = str(args.get("parent_task_id", "") or "").strip()
    if not parent_task_id:
        return _err("parent_task_id is required")

    subtasks_raw = args.get("subtasks", "")
    if isinstance(subtasks_raw, str):
        try:
            subtasks = json.loads(subtasks_raw)
        except json.JSONDecodeError as exc:
            return _err(f"subtasks must be valid JSON array: {exc}")
    elif isinstance(subtasks_raw, list):
        subtasks = subtasks_raw
    else:
        return _err("subtasks must be a JSON array of objects")

    if not isinstance(subtasks, list) or not subtasks:
        return _err("subtasks must be a non-empty array")

    conn = connect_runtime_db(get_activity_db_path())
    conn.row_factory = sqlite3.Row
    try:
        created = task_hub.decompose_task(
            conn,
            parent_task_id=parent_task_id,
            subtasks=subtasks,
        )
    except ValueError as exc:
        return _err(str(exc))
    finally:
        conn.close()

    return _ok(
        {
            "success": True,
            "parent_task_id": parent_task_id,
            "subtasks_created": len(created),
            "subtask_ids": [str(s.get("task_id", "")) for s in created],
        }
    )


# ── Phase 3: Proactive Task Generation ───────────────────────────────────────


@tool(
    name="task_hub_create",
    description=(
        "Create a new task in the Task Hub. Use this for proactive task ideation. "
        "Provide title, description, priority (1-4), labels (array of strings), "
        "and source_kind (e.g., 'reflection', 'proactive')."
    ),
    input_schema={
        "title": str,
        "description": str,
        "priority": int,
        "labels": list,
        "source_kind": str,
        "mission_plan": dict,
    },
)
async def task_hub_create_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    return await _task_hub_create_impl(args)


async def _task_hub_create_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    title = str(args.get("title") or "").strip()
    if not title:
        return _err("title is required")

    description = str(args.get("description") or "").strip()
    priority = args.get("priority")
    try:
        priority = int(priority) if priority is not None else 2
    except ValueError:
        priority = 2

    labels = args.get("labels", [])
    if not isinstance(labels, list):
        labels = [str(labels)]

    source_kind = str(args.get("source_kind", "reflection") or "reflection").strip()
    mission_plan = args.get("mission_plan")

    conn = connect_runtime_db(get_activity_db_path())
    conn.row_factory = sqlite3.Row
    try:
        import uuid
        task_id = f"task_{uuid.uuid4().hex[:12]}"

        if isinstance(mission_plan, dict) and mission_plan.get("phases") and task_hub_missions_enabled():
            created = task_hub.create_mission_envelope(
                conn,
                task_id=task_id,
                title=title,
                description=description,
                source_kind=source_kind,
                source_ref=task_id,
                project_key="immediate",
                priority=priority,
                labels=labels,
                mission_plan=mission_plan,
                metadata={"mission_origin_source_kind": source_kind},
            )
        else:
            item = {
                "task_id": task_id,
                "title": title,
                "description": description,
                "priority": priority,
                "labels": labels,
                "source_kind": source_kind,
                "status": "open",
                "agent_ready": True,
                "trigger_type": "autonomous",
            }

            created = task_hub.upsert_item(conn, item)
    except Exception as exc:
        return _err(str(exc))
    finally:
        conn.close()

    return _ok({
        "success": True,
        "task_id": task_id,
        "item": created,
    })
