from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional


try:
    from todoist_api_python.api import TodoistAPI
except Exception:  # pragma: no cover
    TodoistAPI = None  # type: ignore


PRIORITY_TO_API = {"urgent": 4, "high": 3, "medium": 2, "low": 1}
API_TO_DISPLAY = {4: "P1-Urgent", 3: "P2-High", 2: "P3-Medium", 1: "P4-Low"}
DISPLAY_TO_API = {
    "P1": 4,
    "P2": 3,
    "P3": 2,
    "P4": 1,
    "P1-Urgent": 4,
    "P2-High": 3,
    "P3-Medium": 2,
    "P4-Low": 1,
}


AGENT_TASKS_PROJECT = "Agent Tasks"
BRAINSTORM_PROJECT = "UA Brainstorm Pipeline"

AGENT_SECTIONS = {
    "immediate": "Immediate",
    "scheduled": "Scheduled",
    "background": "Background",
    "recurring": "Recurring",
}

BRAINSTORM_SECTIONS = {
    "inbox": "Inbox",
    "triaging": "Triaging",
    "heartbeat_candidate": "Heartbeat Candidate",
    "approved": "Approved for Build",
    "in_implementation": "In Implementation",
    "parked": "Parked / Rejected",
}

DEFAULT_AGENT_LABELS = [
    "agent-ready",
    "needs-review",
    "blocked",
    "sub-agent:research",
    "sub-agent:writer",
    "sub-agent:code",
]

DEFAULT_BRAINSTORM_LABELS = [
    "brainstorm",
    "heartbeat-candidate",
    "needs-spec",
    "approved",
]


@dataclass
class TodoistTaxonomy:
    agent_project_id: str
    brainstorm_project_id: str
    agent_sections: dict[str, str]
    brainstorm_sections: dict[str, str]


class TodoService:
    """Pure Todoist API service. No LLM/tool coupling."""

    def __init__(self, api_token: str | None = None, *, api: Optional[object] = None):
        if api_token is None:
            token = (
                os.getenv("TODOIST_API_TOKEN")
                or os.getenv("TODOIST_API_KEY")
                or ""
            ).strip()
        else:
            token = str(api_token).strip()
        if api is None:
            if not token:
                raise ValueError("TODOIST_API_TOKEN is required")
            if TodoistAPI is None:
                raise RuntimeError("todoist-api-python is not installed")
            self._api = TodoistAPI(token)
        else:
            self._api = api

        self._taxonomy: Optional[TodoistTaxonomy] = None

    @property
    def api(self):
        return self._api

    def ensure_taxonomy(self) -> dict:
        """Idempotently create projects, sections, labels."""

        try:
            projects = _collect_items(self.api.get_projects())
            fallback_project = projects[0] if projects else None
            agent_project = _find_by_name(projects, AGENT_TASKS_PROJECT)
            if not agent_project:
                try:
                    agent_project = self.api.add_project(name=AGENT_TASKS_PROJECT)
                except Exception:
                    agent_project = fallback_project

            brainstorm_project = _find_by_name(projects, BRAINSTORM_PROJECT)
            if not brainstorm_project:
                try:
                    brainstorm_project = self.api.add_project(name=BRAINSTORM_PROJECT)
                except Exception:
                    brainstorm_project = agent_project or fallback_project

            if not agent_project or not brainstorm_project:
                raise RuntimeError("Todoist projects unavailable")

            agent_project_id = str(_get_field(agent_project, "id", "") or "")
            brainstorm_project_id = str(_get_field(brainstorm_project, "id", "") or "")
            if not agent_project_id or not brainstorm_project_id:
                raise RuntimeError("Todoist project ids unavailable")

            agent_sections = self._ensure_sections(agent_project_id, AGENT_SECTIONS)
            brainstorm_sections = self._ensure_sections(brainstorm_project_id, BRAINSTORM_SECTIONS)

            labels_created: list[str] = []
            labels = _collect_items(self.api.get_labels())
            existing_labels = {
                str(_get_field(lbl, "name", "") or "").strip()
                for lbl in labels
                if str(_get_field(lbl, "name", "") or "").strip()
            }
            for name in [*DEFAULT_AGENT_LABELS, *DEFAULT_BRAINSTORM_LABELS]:
                if name not in existing_labels:
                    try:
                        self.api.add_label(name=name)
                        labels_created.append(name)
                    except Exception:
                        # Another actor may have created it; treat as idempotent.
                        pass

            self._taxonomy = TodoistTaxonomy(
                agent_project_id=agent_project_id,
                brainstorm_project_id=brainstorm_project_id,
                agent_sections=agent_sections,
                brainstorm_sections=brainstorm_sections,
            )

            return {
                "agent_project_id": agent_project_id,
                "brainstorm_project_id": brainstorm_project_id,
                "agent_sections": agent_sections,
                "brainstorm_sections": brainstorm_sections,
                "labels_created": labels_created,
            }
        except Exception:
            # Never crash callers (heartbeat). Surface empty-ish payload.
            return {
                "agent_project_id": "",
                "brainstorm_project_id": "",
                "agent_sections": {},
                "brainstorm_sections": {},
                "labels_created": [],
            }

    def get_actionable_tasks(self, filter_str: str | None = None) -> list[dict[str, Any]]:
        """Default filter: '(overdue | today | no date) & @agent-ready & !@blocked'."""

        try:
            filter_value = (
                (filter_str or "").strip()
                or "(overdue | today | no date) & @agent-ready & !@blocked"
            )

            tasks: list[object]
            if not (filter_str or "").strip():
                # SDK compatibility path: newer API prefers keyword args (e.g. label)
                # and does not accept filter=. Pull agent-ready tasks, then apply
                # deterministic local filtering for blocked/due semantics.
                tasks = _collect_items(self.api.get_tasks(label="agent-ready"))
            else:
                try:
                    # Backward-compatible path for SDKs supporting Todoist filter syntax.
                    tasks = _collect_items(self.api.get_tasks(filter=filter_value))
                except TypeError:
                    tasks = _collect_items(self.api.get_tasks())

            out = [self._task_to_dict(task) for task in tasks]
            out = _apply_local_filter(out, filter_value)
            out.sort(
                key=lambda row: (
                    -int(DISPLAY_TO_API.get(str(row.get("priority") or ""), 1)),
                    str(row.get("due_date") or "9999-99-99"),
                )
            )
            return out
        except Exception:
            return []

    def get_all_tasks(
        self, project_id: str | None = None, label: str | None = None
    ) -> list[dict[str, Any]]:
        try:
            kwargs: dict[str, Any] = {}
            if project_id:
                kwargs["project_id"] = project_id
            if label:
                kwargs["label"] = label
            tasks = _collect_items(self.api.get_tasks(**kwargs))
            return [self._task_to_dict(task) for task in tasks]
        except Exception:
            return []

    def get_task_detail(self, task_id: str) -> dict[str, Any] | None:
        try:
            task = self.api.get_task(task_id)
            base = self._task_to_dict(task)
            comments = []
            try:
                pages = self.api.get_comments(task_id=task_id)
                for page in pages or []:
                    for comment in page or []:
                        comments.append(
                            {
                                "id": str(getattr(comment, "id", "")),
                                "content": str(getattr(comment, "content", "")),
                                "posted_at": str(getattr(comment, "posted_at", "")),
                            }
                        )
            except Exception:
                comments = []
            base["comments"] = comments
            return base
        except Exception:
            return None

    def create_task(
        self,
        content: str,
        description: str = "",
        priority: str = "low",
        section: str = "background",
        labels: list[str] | None = None,
        due_string: str | None = None,
        sub_agent: str | None = None,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        taxonomy = self._get_taxonomy_or_bootstrap()
        section_id = taxonomy.agent_sections.get(section.lower())
        task_labels = set(labels or [])
        task_labels.add("agent-ready")
        if sub_agent:
            task_labels.add(f"sub-agent:{sub_agent}")
        api_priority = PRIORITY_TO_API.get(priority.lower(), 1)
        task = self.api.add_task(
            content=content,
            description=description,
            project_id=taxonomy.agent_project_id,
            section_id=section_id,
            labels=sorted(task_labels),
            priority=api_priority,
            due_string=due_string,
            parent_id=parent_id,
        )
        return self._task_to_dict(task)

    def update_task(self, task_id: str, **kwargs) -> bool:
        try:
            self.api.update_task(task_id=task_id, **kwargs)
            return True
        except Exception:
            return False

    def complete_task(self, task_id: str, summary: str | None = None) -> bool:
        try:
            self.api.close_task(task_id)
            if summary:
                self.add_comment(task_id, f"**Agent note:** {summary}")
            return True
        except Exception:
            return False

    def delete_task(self, task_id: str) -> bool:
        try:
            self.api.delete_task(task_id)
            return True
        except Exception:
            return False

    def add_comment(self, task_id: str, content: str) -> bool:
        try:
            self.api.add_comment(task_id=task_id, content=content)
            return True
        except Exception:
            return False

    def mark_blocked(self, task_id: str, reason: str) -> bool:
        return self._swap_labels(
            task_id,
            remove={"agent-ready"},
            add={"blocked"},
            comment=f"**Blocked:** {reason}",
        )

    def unblock_task(self, task_id: str) -> bool:
        return self._swap_labels(task_id, remove={"blocked"}, add={"agent-ready"}, comment="Unblocked")

    def mark_needs_review(self, task_id: str, result_summary: str) -> bool:
        return self._swap_labels(
            task_id,
            remove={"agent-ready"},
            add={"needs-review"},
            comment=f"**Needs review:** {result_summary}",
        )

    def heartbeat_summary(self) -> dict[str, Any]:
        """Deterministic summary for heartbeat integration. Never raises."""

        now = datetime.now(timezone.utc).isoformat()
        tasks = self.get_actionable_tasks()
        by_priority: dict[str, list[str]] = {}
        by_sub_agent: dict[str, list[str]] = {}
        for task in tasks:
            tid = str(task.get("id") or "")
            if not tid:
                continue
            pr = str(task.get("priority") or "P4-Low")
            by_priority.setdefault(pr, []).append(tid)
            sub = str(task.get("sub_agent") or "unrouted")
            by_sub_agent.setdefault(sub, []).append(tid)

        brainstorm_counts: dict[str, int] = {}
        try:
            brainstorm_counts = self.get_pipeline_summary()
        except Exception:
            brainstorm_counts = {}

        return {
            "timestamp": now,
            "actionable_count": len(tasks),
            "overdue_count": 0,
            "tasks": [
                {
                    "id": t.get("id"),
                    "content": t.get("content"),
                    "priority": t.get("priority"),
                    "sub_agent": t.get("sub_agent"),
                    "due_date": t.get("due_date"),
                    "section": t.get("section"),
                }
                for t in tasks
            ],
            "by_sub_agent": by_sub_agent,
            "by_priority": by_priority,
            "brainstorm_pipeline": brainstorm_counts,
            "summary": f"{len(tasks)} actionable tasks",
        }

    def record_idea(
        self,
        content: str,
        description: str = "",
        dedupe_key: str | None = None,
        source_session_id: str | None = None,
        source_trace_id: str | None = None,
        impact: str = "M",
        effort: str = "M",
    ) -> dict[str, Any]:
        taxonomy = self._get_taxonomy_or_bootstrap()
        inbox_section_id = taxonomy.brainstorm_sections.get("inbox")
        clean_key = (dedupe_key or "").strip() or None
        payload_description = _format_idea_description(
            description=description,
            dedupe_key=clean_key,
            source_session_id=source_session_id,
            source_trace_id=source_trace_id,
            impact=impact,
            effort=effort,
            confidence=1,
        )

        if clean_key:
            existing = self._find_existing_idea_by_dedupe_key(clean_key)
            if existing is not None:
                existing_description = str(existing.get("description") or "")
                frontmatter, body = _parse_frontmatter(existing_description)
                confidence = _safe_int(frontmatter.get("confidence"), default=1) + 1
                frontmatter["confidence"] = confidence
                updated_description = _serialize_frontmatter(frontmatter, body)
                self.update_task(str(existing.get("id") or ""), description=updated_description)
                comment_parts = ["**Idea resurfaced**"]
                comment_parts.append(f"dedupe_key={clean_key}")
                if source_session_id:
                    comment_parts.append(f"source_session={source_session_id}")
                if source_trace_id:
                    comment_parts.append(f"source_trace={source_trace_id}")
                if description:
                    comment_parts.append(description)
                self.add_comment(str(existing.get("id") or ""), "\n".join(comment_parts))
                existing["description"] = updated_description
                return existing

        task = self.api.add_task(
            content=content,
            description=payload_description,
            project_id=taxonomy.brainstorm_project_id,
            section_id=inbox_section_id,
            labels=["brainstorm"],
            priority=1,
        )
        return self._task_to_dict(task)

    def promote_idea(self, task_id: str, target_section: str = "approved") -> bool:
        taxonomy = self._get_taxonomy_or_bootstrap()
        section_id = taxonomy.brainstorm_sections.get((target_section or "").strip().lower())
        if not section_id:
            return False
        return self.update_task(task_id, section_id=section_id)

    def park_idea(self, task_id: str, rationale: str) -> bool:
        ok = self.promote_idea(task_id, target_section="parked")
        if not ok:
            return False
        if rationale:
            self.add_comment(task_id, f"**Parked / Rejected:** {rationale}")
        return True

    def promote_idea_to_heartbeat_candidate(self, target: str) -> dict[str, Any]:
        """Promote a brainstorm idea to heartbeat_candidate via task id or dedupe key."""
        token = str(target or "").strip()
        if not token:
            return {
                "success": False,
                "error": "target is required (task id or dedupe key)",
            }

        task_id_hint: Optional[str] = None
        dedupe_hint: Optional[str] = None
        if ":" in token:
            prefix, value = token.split(":", 1)
            key = prefix.strip().lower()
            val = value.strip()
            if key in {"id", "task", "task_id"}:
                task_id_hint = val
            elif key in {"dedupe", "key", "dedupe_key"}:
                dedupe_hint = val

        if not task_id_hint and not dedupe_hint:
            task_id_hint = token

        task: Optional[dict[str, Any]] = None
        if task_id_hint:
            task = self.get_task_detail(task_id_hint)

        if task is None:
            key = (dedupe_hint or token).strip()
            if key:
                task = self._find_existing_idea_by_dedupe_key(key)

        if not task:
            return {
                "success": False,
                "error": "idea not found",
                "target": token,
            }

        task_id = str(task.get("id") or "")
        if not task_id:
            return {
                "success": False,
                "error": "resolved idea has no id",
                "target": token,
            }

        taxonomy = self._get_taxonomy_or_bootstrap()
        reverse = {v: k for k, v in taxonomy.brainstorm_sections.items()}
        previous_section = reverse.get(str(task.get("section_id") or "")) or ""
        ok = self.promote_idea(task_id, target_section="heartbeat_candidate")
        return {
            "success": bool(ok),
            "task_id": task_id,
            "content": str(task.get("content") or ""),
            "previous_section": previous_section,
            "target_section": "heartbeat_candidate",
            "already_candidate": previous_section == "heartbeat_candidate",
        }

    def get_pipeline_summary(self) -> dict[str, int]:
        taxonomy = self._get_taxonomy_or_bootstrap()
        reverse = {v: k for k, v in taxonomy.brainstorm_sections.items()}
        tasks = self.get_all_tasks(project_id=taxonomy.brainstorm_project_id)
        counts: dict[str, int] = {k: 0 for k in taxonomy.brainstorm_sections.keys()}
        for task in tasks:
            sid = str(task.get("section_id") or "")
            key = reverse.get(sid)
            if not key:
                continue
            counts[key] = int(counts.get(key) or 0) + 1
        return counts

    def heartbeat_brainstorm_candidates(
        self,
        limit: int = 3,
        *,
        include_sections: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """Return shortlist of brainstorm items suitable for proactive heartbeat investigation.

        Default policy is conservative: only `heartbeat_candidate` items are surfaced.
        Additional sections can be enabled via explicit call-site intent or
        UA_TODOIST_HEARTBEAT_SECTIONS (comma-separated section keys).
        """
        taxonomy = self._get_taxonomy_or_bootstrap()
        tasks = self.get_all_tasks(project_id=taxonomy.brainstorm_project_id, label="brainstorm")
        reverse = {v: k for k, v in taxonomy.brainstorm_sections.items()}

        configured_sections = include_sections
        if configured_sections is None:
            raw_sections = str(os.getenv("UA_TODOIST_HEARTBEAT_SECTIONS") or "").strip()
            if raw_sections:
                configured_sections = [
                    part.strip().lower()
                    for part in raw_sections.split(",")
                    if part.strip()
                ]
            else:
                configured_sections = ["heartbeat_candidate"]

        allowed_sections = {
            section
            for section in (configured_sections or [])
            if section in taxonomy.brainstorm_sections
        }
        if not allowed_sections:
            allowed_sections = {"heartbeat_candidate"}

        section_rank = {
            section: rank
            for rank, section in enumerate(["heartbeat_candidate", "approved", "triaging", "inbox"])
        }

        candidates: list[dict[str, Any]] = []
        for task in tasks:
            section_id = str(task.get("section_id") or "")
            section_key = reverse.get(section_id) or ""
            if section_key not in allowed_sections:
                continue

            frontmatter, _ = _parse_frontmatter(str(task.get("description") or ""))
            confidence = _safe_int(frontmatter.get("confidence"), default=1)
            candidates.append(
                {
                    "id": str(task.get("id") or ""),
                    "content": str(task.get("content") or ""),
                    "section": section_key,
                    "confidence": confidence,
                    "impact": str(frontmatter.get("impact") or "M"),
                    "effort": str(frontmatter.get("effort") or "M"),
                    "dedupe_key": str(frontmatter.get("dedupe_key") or "") or None,
                    "url": str(task.get("url") or ""),
                }
            )

        candidates.sort(
            key=lambda row: (
                int(section_rank.get(str(row.get("section") or ""), 99)),
                -int(row.get("confidence") or 1),
            )
        )
        n = max(1, int(limit or 1))
        return candidates[:n]

    def _get_taxonomy_or_bootstrap(self) -> TodoistTaxonomy:
        if self._taxonomy is None:
            self.ensure_taxonomy()
        if self._taxonomy is None:
            raise RuntimeError("Todoist taxonomy unavailable")
        return self._taxonomy

    def _ensure_sections(self, project_id: str, desired: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        existing = _collect_items(self.api.get_sections(project_id=project_id))
        for key, name in desired.items():
            found = _find_by_name(existing, name)
            if not found:
                try:
                    found = self.api.add_section(name=name, project_id=project_id)
                except Exception:
                    found = None
            out[key] = str(_get_field(found, "id", "") or "")
        return out

    def _find_existing_idea_by_dedupe_key(self, dedupe_key: str) -> Optional[dict[str, Any]]:
        taxonomy = self._get_taxonomy_or_bootstrap()
        tasks = self.get_all_tasks(project_id=taxonomy.brainstorm_project_id)
        needle = f"dedupe_key: {dedupe_key}"
        for task in tasks:
            desc = str(task.get("description") or "")
            if needle in desc:
                return task
        return None

    def _swap_labels(self, task_id: str, *, remove: set[str], add: set[str], comment: str) -> bool:
        try:
            task = self.api.get_task(task_id)
            labels = set(getattr(task, "labels", []) or [])
            labels.difference_update(remove)
            labels.update(add)
            self.api.update_task(task_id=task_id, labels=sorted(labels))
            self.add_comment(task_id, comment)
            return True
        except Exception:
            return False

    def _task_to_dict(self, task: object) -> dict[str, Any]:
        labels = list(getattr(task, "labels", []) or [])
        due = getattr(task, "due", None)
        due_date = getattr(due, "date", None) if due else None
        due_datetime = getattr(due, "datetime", None) if due else None
        is_recurring = bool(getattr(due, "is_recurring", False)) if due else False

        sub_agent: Optional[str] = None
        for label in labels:
            if isinstance(label, str) and label.startswith("sub-agent:"):
                sub_agent = label.split(":", 1)[1] or None
                break

        priority_num = int(getattr(task, "priority", 1) or 1)
        priority_display = API_TO_DISPLAY.get(priority_num, "P4-Low")

        return {
            "id": str(getattr(task, "id", "")),
            "content": str(getattr(task, "content", "")),
            "description": str(getattr(task, "description", "")),
            "priority": priority_display,
            "project_id": str(getattr(task, "project_id", "")),
            "section_id": str(getattr(task, "section_id", "")) if getattr(task, "section_id", None) else None,
            "parent_id": str(getattr(task, "parent_id", "")) if getattr(task, "parent_id", None) else None,
            "labels": labels,
            "due_date": due_date,
            "due_datetime": due_datetime,
            "is_recurring": is_recurring,
            "sub_agent": sub_agent,
            "url": str(getattr(task, "url", "")),
            "created_at": str(getattr(task, "created_at", "")),
            "comment_count": int(getattr(task, "comment_count", 0) or 0),
        }


def _find_by_name(items: list[object], name: str) -> Optional[object]:
    for item in items:
        if str(_get_field(item, "name", "") or "").strip() == name:
            return item
    return None


def _collect_items(values: object) -> list[object]:
    out: list[object] = []
    for item in list(values or []):
        if isinstance(item, list):
            out.extend(item)
        else:
            out.append(item)
    return out


def _get_field(item: object, field: str, default: Any = None) -> Any:
    if item is None:
        return default
    if isinstance(item, dict):
        return item.get(field, default)
    return getattr(item, field, default)


def _apply_local_filter(rows: list[dict[str, Any]], filter_value: str) -> list[dict[str, Any]]:
    q = (filter_value or "").strip().lower()
    if not q:
        return rows

    out = list(rows)
    wants_agent_ready = "@agent-ready" in q
    wants_blocked = "@blocked" in q and "!@blocked" not in q
    excludes_blocked = "!@blocked" in q
    wants_due_window = any(token in q for token in ("overdue", "today", "no date"))

    if wants_agent_ready:
        out = [row for row in out if "agent-ready" in (row.get("labels") or [])]
    if wants_blocked:
        out = [row for row in out if "blocked" in (row.get("labels") or [])]
    if excludes_blocked:
        out = [row for row in out if "blocked" not in (row.get("labels") or [])]
    if wants_due_window:
        out = [row for row in out if _matches_due_window(row)]

    return out


def _matches_due_window(row: dict[str, Any]) -> bool:
    due = str(row.get("due_date") or "").strip()
    if not due:
        return True
    today = datetime.now(timezone.utc).date().isoformat()
    return due <= today


def _format_idea_description(
    *,
    description: str,
    dedupe_key: Optional[str],
    source_session_id: Optional[str],
    source_trace_id: Optional[str],
    impact: str,
    effort: str,
    confidence: int,
) -> str:
    frontmatter: dict[str, Any] = {
        "dedupe_key": dedupe_key,
        "source_session": source_session_id,
        "source_trace": source_trace_id,
        "impact": (impact or "M").strip() or "M",
        "effort": (effort or "M").strip() or "M",
        "confidence": int(confidence or 1),
    }
    # Remove None/empty values for readability.
    cleaned = {k: v for k, v in frontmatter.items() if v not in (None, "")}
    return _serialize_frontmatter(cleaned, description or "")


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    value = text or ""
    if not value.startswith("---"):
        return {}, value
    parts = value.split("---", 2)
    if len(parts) < 3:
        return {}, value
    raw = parts[1].strip("\n")
    body = parts[2].lstrip("\n")
    data: dict[str, Any] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if val.isdigit():
            data[key] = int(val)
        else:
            data[key] = val
    return data, body


def _serialize_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    lines = ["---"]
    for key, val in frontmatter.items():
        if val is None or val == "":
            continue
        lines.append(f"{key}: {val}")
    lines.append("---")
    if body:
        return "\n".join(lines) + "\n" + body.strip() + "\n"
    return "\n".join(lines) + "\n"


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default
