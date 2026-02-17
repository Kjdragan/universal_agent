from __future__ import annotations

import json
from typing import Any, Dict

from claude_agent_sdk import tool


def _service():
    from universal_agent.services.todoist_service import TodoService

    return TodoService()


def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=True)}]}


def _err(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": f"error: {message}"}]}


@tool(
    name="todoist_setup",
    description="Idempotently ensure Todoist taxonomy (projects/sections/labels) exists.",
    input_schema={},
)
async def todoist_setup_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    return await _todoist_setup_impl(args)


async def _todoist_setup_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    del args
    svc = _service()
    return _ok(svc.ensure_taxonomy())


@tool(
    name="todoist_query",
    description="Query actionable Todoist tasks. Optional custom filter string.",
    input_schema={"filter": str},
)
async def todoist_query_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    return await _todoist_query_impl(args)


async def _todoist_query_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    svc = _service()
    filter_str = str(args.get("filter", "") or "").strip() or None
    tasks = svc.get_actionable_tasks(filter_str=filter_str)
    return _ok({"tasks": tasks, "count": len(tasks)})


@tool(
    name="todoist_get_task",
    description="Get Todoist task details including comments by task_id.",
    input_schema={"task_id": str},
)
async def todoist_get_task_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    return await _todoist_get_task_impl(args)


async def _todoist_get_task_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    task_id = str(args.get("task_id", "") or "").strip()
    if not task_id:
        return _err("task_id is required")
    svc = _service()
    detail = svc.get_task_detail(task_id)
    if detail is None:
        return _err(f"task not found: {task_id}")
    return _ok({"task": detail})


@tool(
    name="todoist_task_action",
    description=(
        "Mutate Todoist tasks. Actions: create, update, complete, delete, comment, block, unblock, review."
    ),
    input_schema={
        "action": str,
        "task_id": str,
        "content": str,
        "description": str,
        "priority": str,
        "section": str,
        "labels": list,
        "due_string": str,
        "sub_agent": str,
        "summary": str,
        "reason": str,
        "text": str,
        "fields": dict,
    },
)
async def todoist_task_action_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    return await _todoist_task_action_impl(args)


async def _todoist_task_action_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    svc = _service()
    action = str(args.get("action", "") or "").strip().lower()

    if action == "create":
        content = str(args.get("content", "") or "").strip()
        if not content:
            return _err("content is required for create")
        task = svc.create_task(
            content=content,
            description=str(args.get("description", "") or ""),
            priority=str(args.get("priority", "low") or "low"),
            section=str(args.get("section", "background") or "background"),
            labels=list(args.get("labels") or []),
            due_string=(str(args.get("due_string", "") or "").strip() or None),
            sub_agent=(str(args.get("sub_agent", "") or "").strip() or None),
        )
        return _ok({"success": True, "action": action, "task": task})

    task_id = str(args.get("task_id", "") or "").strip()
    if not task_id:
        return _err("task_id is required for this action")

    if action == "complete":
        ok = svc.complete_task(task_id, summary=(str(args.get("summary", "") or "").strip() or None))
        return _ok({"success": ok, "action": action, "task_id": task_id})

    if action == "delete":
        ok = svc.delete_task(task_id)
        return _ok({"success": ok, "action": action, "task_id": task_id})

    if action == "comment":
        text = str(args.get("text", "") or "").strip()
        if not text:
            return _err("text is required for comment")
        ok = svc.add_comment(task_id, text)
        return _ok({"success": ok, "action": action, "task_id": task_id})

    if action == "block":
        reason = str(args.get("reason", "") or "").strip()
        if not reason:
            return _err("reason is required for block")
        ok = svc.mark_blocked(task_id, reason)
        return _ok({"success": ok, "action": action, "task_id": task_id})

    if action == "unblock":
        ok = svc.unblock_task(task_id)
        return _ok({"success": ok, "action": action, "task_id": task_id})

    if action == "review":
        summary = str(args.get("summary", "") or "").strip()
        if not summary:
            return _err("summary is required for review")
        ok = svc.mark_needs_review(task_id, summary)
        return _ok({"success": ok, "action": action, "task_id": task_id})

    if action == "update":
        fields = args.get("fields")
        if not isinstance(fields, dict) or not fields:
            return _err("fields dict is required for update")
        ok = svc.update_task(task_id, **fields)
        return _ok({"success": ok, "action": action, "task_id": task_id})

    return _err(f"unsupported action: {action}")


@tool(
    name="todoist_idea_action",
    description="Brainstorm pipeline actions: record, promote, park, pipeline.",
    input_schema={
        "action": str,
        "content": str,
        "description": str,
        "dedupe_key": str,
        "source_session_id": str,
        "source_trace_id": str,
        "impact": str,
        "effort": str,
        "task_id": str,
        "target_section": str,
        "rationale": str,
    },
)
async def todoist_idea_action_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    return await _todoist_idea_action_impl(args)


async def _todoist_idea_action_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    svc = _service()
    action = str(args.get("action", "") or "").strip().lower()

    if action == "pipeline":
        return _ok({"counts": svc.get_pipeline_summary()})

    if action == "record":
        content = str(args.get("content", "") or "").strip()
        if not content:
            return _err("content is required for record")
        task = svc.record_idea(
            content=content,
            description=str(args.get("description", "") or ""),
            dedupe_key=(str(args.get("dedupe_key", "") or "").strip() or None),
            source_session_id=(str(args.get("source_session_id", "") or "").strip() or None),
            source_trace_id=(str(args.get("source_trace_id", "") or "").strip() or None),
            impact=str(args.get("impact", "M") or "M"),
            effort=str(args.get("effort", "M") or "M"),
        )
        return _ok({"success": True, "action": action, "task": task})

    task_id = str(args.get("task_id", "") or "").strip()
    if not task_id:
        return _err("task_id is required for this idea action")

    if action == "promote":
        target_section = str(args.get("target_section", "approved") or "approved")
        ok = svc.promote_idea(task_id, target_section=target_section)
        return _ok({"success": ok, "action": action, "task_id": task_id, "target_section": target_section})

    if action == "park":
        rationale = str(args.get("rationale", "") or "").strip()
        if not rationale:
            return _err("rationale is required for park")
        ok = svc.park_idea(task_id, rationale)
        return _ok({"success": ok, "action": action, "task_id": task_id})

    return _err(f"unsupported idea action: {action}")
