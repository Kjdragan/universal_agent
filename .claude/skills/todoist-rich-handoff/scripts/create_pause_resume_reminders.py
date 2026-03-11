#!/usr/bin/env python3
"""Create/update high-context Todoist pause/resume reminders.

Defaults are tuned for personal-only handoff tasks that should not be picked up
by heartbeat auto-work.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from universal_agent.services.todoist_service import PROJECT_KEY_MAP, UA_PROJECT_IMMEDIATE, TodoService


DEFAULT_LABELS = ["personal-reminder", "sleep-handoff", "no-auto-exec"]
DEFAULT_COMMENT = "Resume context captured by Codex handoff"


@dataclass
class ReminderSpec:
    content: str
    description: str


def _load_specs_from_file(path: Path) -> list[ReminderSpec]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        rows = payload.get("tasks")
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError("tasks payload must be a list or {\"tasks\": [...]}")
    out: list[ReminderSpec] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        content = str(row.get("content") or "").strip()
        description = str(row.get("description") or "").strip()
        if not content:
            continue
        out.append(ReminderSpec(content=content, description=description))
    if not out:
        raise ValueError("no valid tasks found")
    return out


def _preset_threads_corp(commits: list[str]) -> list[ReminderSpec]:
    commit_lines = "\n".join([f"- {sha}" for sha in commits if sha.strip()]) or "- (none listed)"
    return [
        ReminderSpec(
            content="Resume Threads rollout after Phase 1 completion checks",
            description=(
                "Current status: Phase 1 rollout active on VPS, probes healthy.\n"
                "Next work: continue post-Phase-1 hardening and rollout verification.\n\n"
                "Docs:\n"
                "- /home/kjdragan/lrepos/universal_agent/CSI_Ingester/development/THREADS_INFISICAL_SETUP.md\n"
                "- /home/kjdragan/lrepos/universal_agent/docs/004_THREADS_INFISICAL_SYNC_WORKFLOW.md\n"
                "- /home/kjdragan/lrepos/universal_agent/CSI_Ingester/development/README.md\n\n"
                "Recent rollout commits:\n"
                f"{commit_lines}"
            ),
        ),
        ReminderSpec(
            content="Resume corporation track development (start at next not-started phase)",
            description=(
                "Restart point: begin Phase 3a work.\n\n"
                "Docs:\n"
                "- /home/kjdragan/lrepos/universal_agent/corporation/status.md\n"
                "- /home/kjdragan/lrepos/universal_agent/corporation/docs/006_MASTER_IMPLEMENTATION_PLAN.md\n"
                "- /home/kjdragan/lrepos/universal_agent/corporation/docs/phases/phase_3a_generalized_consumer.md"
            ),
        ),
    ]


def _ensure_labels(svc: TodoService, labels: list[str]) -> None:
    existing = {
        str(getattr(row, "name", "") or "").strip()
        for row in (svc.api.get_labels() or [])
        if str(getattr(row, "name", "") or "").strip()
    }
    for label in labels:
        if label not in existing:
            try:
                svc.api.add_label(name=label)
            except Exception:
                pass


def _existing_tasks_by_content(svc: TodoService, project_id: str) -> dict[str, dict[str, Any]]:
    rows = svc.get_all_tasks(project_id=project_id)
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        content = str(row.get("content") or "").strip()
        if content:
            out[content] = row
    return out


def _ensure_comment_once(svc: TodoService, task_id: str, comment: str) -> bool:
    if not comment.strip():
        return False
    detail = svc.get_task_detail(task_id) or {}
    comments = detail.get("comments") if isinstance(detail.get("comments"), list) else []
    for row in comments:
        if not isinstance(row, dict):
            continue
        if comment.lower() in str(row.get("content") or "").lower():
            return False
    return svc.add_comment(task_id, comment)


def _serialize_task_row(row: dict[str, Any]) -> dict[str, Any]:
    labels = row.get("labels") if isinstance(row.get("labels"), list) else []
    return {
        "id": str(row.get("id") or ""),
        "content": str(row.get("content") or ""),
        "project_id": str(row.get("project_id") or ""),
        "section_id": str(row.get("section_id") or ""),
        "labels": [str(item) for item in labels],
        "has_agent_ready": "agent-ready" in labels,
        "due_date": str(row.get("due_date") or ""),
        "due_datetime": str(row.get("due_datetime") or ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-file", default="", help="JSON file with task specs")
    parser.add_argument("--preset", choices=["threads-corp"], default="")
    parser.add_argument("--threads-commits", default="cc941ac,31f9285,4c0eff6")
    parser.add_argument("--project-key", default="immediate")
    parser.add_argument("--section", default="scheduled")
    parser.add_argument("--labels", default=",".join(DEFAULT_LABELS))
    parser.add_argument("--comment", default=DEFAULT_COMMENT)
    parser.add_argument("--due-local", required=True, help="Local due datetime: YYYY-MM-DDTHH:MM")
    parser.add_argument("--timezone", default="America/Chicago")
    parser.add_argument("--recreate-on-section-mismatch", action="store_true", default=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if bool(args.tasks_file) == bool(args.preset):
        print("ERROR=provide exactly one of --tasks-file or --preset")
        return 2

    try:
        tz = ZoneInfo(str(args.timezone))
    except Exception as exc:
        print(f"ERROR=invalid_timezone:{exc}")
        return 2
    try:
        due_dt = datetime.strptime(str(args.due_local), "%Y-%m-%dT%H:%M").replace(tzinfo=tz)
    except Exception as exc:
        print(f"ERROR=invalid_due_local:{exc}")
        return 2

    labels = [part.strip() for part in str(args.labels).split(",") if part.strip()]
    labels = sorted({label for label in labels if label != "agent-ready"})
    if not labels:
        labels = list(DEFAULT_LABELS)

    if args.tasks_file:
        specs = _load_specs_from_file(Path(args.tasks_file).expanduser())
    else:
        commits = [part.strip() for part in str(args.threads_commits).split(",") if part.strip()]
        specs = _preset_threads_corp(commits)

    svc = TodoService()
    taxonomy = svc.ensure_taxonomy()
    project_name = PROJECT_KEY_MAP.get(str(args.project_key).strip().lower(), UA_PROJECT_IMMEDIATE)
    project_id = str((taxonomy.get("project_ids") or {}).get(project_name) or "").strip()
    section_id = str((((taxonomy.get("section_ids") or {}).get(project_name) or {}).get(str(args.section).strip().lower()) or "")).strip()

    if not project_id:
        print(f"ERROR=missing_project_id:{project_name}")
        return 2
    if not section_id:
        print(f"ERROR=missing_section_id:{project_name}:{args.section}")
        return 2

    _ensure_labels(svc, labels)
    existing_by_content = _existing_tasks_by_content(svc, project_id=project_id)

    created = 0
    updated = 0
    recreated = 0
    deleted = 0
    comment_added = 0
    rows: list[dict[str, Any]] = []

    for spec in specs:
        existing = existing_by_content.get(spec.content)
        action = "none"
        task_id = ""

        if existing is None:
            action = "create"
            if not args.dry_run:
                task = svc.api.add_task(
                    content=spec.content,
                    description=spec.description,
                    project_id=project_id,
                    section_id=section_id,
                    labels=labels,
                    due_datetime=due_dt,
                )
                task_id = str(getattr(task, "id", "") or "").strip()
            created += 1
        else:
            existing_id = str(existing.get("id") or "").strip()
            existing_section = str(existing.get("section_id") or "").strip()
            if existing_section != section_id and args.recreate_on_section_mismatch:
                action = "recreate"
                if not args.dry_run:
                    task = svc.api.add_task(
                        content=spec.content,
                        description=spec.description,
                        project_id=project_id,
                        section_id=section_id,
                        labels=labels,
                        due_datetime=due_dt,
                    )
                    task_id = str(getattr(task, "id", "") or "").strip()
                    if task_id and existing_id:
                        svc.delete_task(existing_id)
                        deleted += 1
                recreated += 1
            else:
                action = "update"
                task_id = existing_id
                if not args.dry_run:
                    svc.api.update_task(
                        task_id=existing_id,
                        description=spec.description,
                        labels=labels,
                        due_datetime=due_dt,
                    )
                updated += 1

        if not task_id:
            task_id = str(existing.get("id") or "") if existing else f"dry-run:{spec.content[:24]}"
        if not args.dry_run and task_id and not task_id.startswith("dry-run:"):
            if _ensure_comment_once(svc, task_id, str(args.comment)):
                comment_added += 1

        detail = svc.get_task_detail(task_id) if (not args.dry_run and not task_id.startswith("dry-run:")) else None
        if isinstance(detail, dict):
            serial = _serialize_task_row(detail)
        else:
            serial = {
                "id": task_id,
                "content": spec.content,
                "project_id": project_id,
                "section_id": section_id,
                "labels": labels,
                "has_agent_ready": "agent-ready" in labels,
                "due_date": "",
                "due_datetime": str(due_dt),
            }
        serial["action"] = action
        rows.append(serial)

    actionable_ids = {
        str(row.get("id") or "")
        for row in svc.get_actionable_tasks()
    } if not args.dry_run else set()

    verification = {
        "all_no_agent_ready": all(not row.get("has_agent_ready", False) for row in rows),
        "all_in_target_project": all(str(row.get("project_id") or "") == project_id for row in rows),
        "all_in_target_section": all(str(row.get("section_id") or "") == section_id for row in rows),
        "all_excluded_from_actionable": all(str(row.get("id") or "") not in actionable_ids for row in rows)
        if not args.dry_run
        else True,
    }

    print(
        json.dumps(
            {
                "dry_run": bool(args.dry_run),
                "project_name": project_name,
                "project_id": project_id,
                "section_key": str(args.section),
                "section_id": section_id,
                "due_local": str(args.due_local),
                "timezone": str(args.timezone),
                "created_count": created,
                "updated_count": updated,
                "recreated_count": recreated,
                "deleted_count": deleted,
                "comment_added_count": comment_added,
                "tasks": rows,
                "verification": verification,
            },
            indent=2,
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

