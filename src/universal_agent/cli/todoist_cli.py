from __future__ import annotations

import json
import sys
from typing import Any, Optional

import click


def _json_out(payload: Any) -> None:
    click.echo(json.dumps(payload, indent=2, sort_keys=True))


def _json_err(message: str, *, exit_code: int = 1) -> None:
    click.echo(json.dumps({"success": False, "error": message}, indent=2, sort_keys=True), err=True)
    raise SystemExit(exit_code)


def _get_service():
    from universal_agent.services.todoist_service import TodoService

    return TodoService()


@click.group(help="Todoist operator CLI (JSON output).")
def cli() -> None:
    pass


@cli.command("setup", help="Idempotently create projects/sections/labels.")
def cmd_setup() -> None:
    svc = _get_service()
    res = svc.ensure_taxonomy()
    _json_out(res)


@cli.command("heartbeat", help="Emit deterministic heartbeat summary JSON.")
def cmd_heartbeat() -> None:
    svc = _get_service()
    res = svc.heartbeat_summary()
    _json_out(res)


@cli.command("tasks", help="List actionable tasks.")
@click.option("--filter", "filter_str", default=None, help="Todoist filter string")
def cmd_tasks(filter_str: Optional[str]) -> None:
    svc = _get_service()
    rows = svc.get_actionable_tasks(filter_str=filter_str)
    _json_out(rows)


@cli.command("task", help="Fetch a single task with comment history.")
@click.argument("task_id")
def cmd_task(task_id: str) -> None:
    svc = _get_service()
    detail = svc.get_task_detail(task_id)
    if detail is None:
        _json_err(f"Task not found: {task_id}", exit_code=2)
    _json_out(detail)


@cli.command("create", help="Create a task in Agent Tasks.")
@click.argument("content")
@click.option("--description", default="")
@click.option("--priority", type=click.Choice(["urgent", "high", "medium", "low"], case_sensitive=False), default="low")
@click.option("--section", default="background")
@click.option("--label", "labels", multiple=True)
@click.option("--due", "due_string", default=None)
@click.option("--sub-agent", default=None)
def cmd_create(
    content: str,
    description: str,
    priority: str,
    section: str,
    labels: tuple[str, ...],
    due_string: Optional[str],
    sub_agent: Optional[str],
) -> None:
    svc = _get_service()
    task = svc.create_task(
        content=content,
        description=description,
        priority=priority,
        section=section,
        labels=list(labels) if labels else None,
        due_string=due_string,
        sub_agent=sub_agent,
    )
    _json_out({"success": True, "task": task})


@cli.command("complete", help="Complete (close) a task.")
@click.argument("task_id")
@click.option("--summary", default=None)
def cmd_complete(task_id: str, summary: Optional[str]) -> None:
    svc = _get_service()
    ok = svc.complete_task(task_id, summary=summary)
    if not ok:
        _json_err(f"Failed to complete task: {task_id}")
    _json_out({"success": True, "task_id": task_id, "action": "completed"})


@cli.command("comment", help="Add a comment to a task.")
@click.argument("task_id")
@click.argument("text")
def cmd_comment(task_id: str, text: str) -> None:
    svc = _get_service()
    ok = svc.add_comment(task_id, text)
    if not ok:
        _json_err(f"Failed to add comment: {task_id}")
    _json_out({"success": True, "task_id": task_id, "action": "commented"})


@cli.command("block", help="Mark a task blocked (remove agent-ready, add blocked).")
@click.argument("task_id")
@click.option("--reason", required=True)
def cmd_block(task_id: str, reason: str) -> None:
    svc = _get_service()
    ok = svc.mark_blocked(task_id, reason)
    if not ok:
        _json_err(f"Failed to block task: {task_id}")
    _json_out({"success": True, "task_id": task_id, "action": "blocked"})


@cli.command("unblock", help="Unblock a task (remove blocked, add agent-ready).")
@click.argument("task_id")
def cmd_unblock(task_id: str) -> None:
    svc = _get_service()
    ok = svc.unblock_task(task_id)
    if not ok:
        _json_err(f"Failed to unblock task: {task_id}")
    _json_out({"success": True, "task_id": task_id, "action": "unblocked"})


@cli.command("review", help="Mark a task needs review (remove agent-ready, add needs-review).")
@click.argument("task_id")
@click.option("--summary", required=True)
def cmd_review(task_id: str, summary: str) -> None:
    svc = _get_service()
    ok = svc.mark_needs_review(task_id, summary)
    if not ok:
        _json_err(f"Failed to mark needs-review: {task_id}")
    _json_out({"success": True, "task_id": task_id, "action": "needs_review"})


@cli.command("idea", help="Capture an idea in the brainstorm pipeline (dedupe-aware).")
@click.argument("content")
@click.option("--description", default="")
@click.option("--dedupe-key", default=None)
@click.option("--source-session", default=None)
@click.option("--source-trace", default=None)
@click.option("--impact", type=click.Choice(["H", "M", "L"], case_sensitive=False), default="M")
@click.option("--effort", type=click.Choice(["S", "M", "L"], case_sensitive=False), default="M")
def cmd_idea(
    content: str,
    description: str,
    dedupe_key: Optional[str],
    source_session: Optional[str],
    source_trace: Optional[str],
    impact: str,
    effort: str,
) -> None:
    svc = _get_service()
    task = svc.record_idea(
        content=content,
        description=description,
        dedupe_key=dedupe_key,
        source_session_id=source_session,
        source_trace_id=source_trace,
        impact=impact,
        effort=effort,
    )
    _json_out({"success": True, "task": task})


@cli.command("promote", help="Move brainstorm idea to a target section key.")
@click.argument("task_id")
@click.option("--to", "target", default="approved")
def cmd_promote(task_id: str, target: str) -> None:
    svc = _get_service()
    ok = svc.promote_idea(task_id, target_section=target)
    if not ok:
        _json_err(f"Failed to promote idea: {task_id}")
    _json_out({"success": True, "task_id": task_id, "action": "promoted", "to": target})


@cli.command("park", help="Park/reject an idea with rationale.")
@click.argument("task_id")
@click.option("--rationale", required=True)
def cmd_park(task_id: str, rationale: str) -> None:
    svc = _get_service()
    ok = svc.park_idea(task_id, rationale)
    if not ok:
        _json_err(f"Failed to park idea: {task_id}")
    _json_out({"success": True, "task_id": task_id, "action": "parked"})


@cli.command("pipeline", help="Return brainstorm pipeline section counts.")
def cmd_pipeline() -> None:
    svc = _get_service()
    counts = svc.get_pipeline_summary()
    _json_out(counts)


def main(argv: Optional[list[str]] = None) -> int:
    try:
        cli.main(args=argv, prog_name="todoist", standalone_mode=False)
        return 0
    except SystemExit as exc:
        code = int(getattr(exc, "code", 1) or 0)
        return code
    except Exception as exc:
        click.echo(json.dumps({"success": False, "error": str(exc)}, indent=2, sort_keys=True), err=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
