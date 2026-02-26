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


# ── 5-Project UA Taxonomy ──────────────────────────────────────────────────────
# Each project has a distinct purpose. All human and agent tasks route here.

# Project 1: Core missions rooted in soul doc, identity, values, and long-horizon goals.
UA_PROJECT_MISSION = "UA: Mission & Identity"
# Project 2: Tasks surfaced from memory analysis, profile updates, or knowledge gaps.
UA_PROJECT_MEMORY = "UA: Memory Insights"
# Project 3: Heartbeat / cron-schedulable opportunities — Simone can self-dispatch.
UA_PROJECT_PROACTIVE = "UA: Proactive Intelligence"
# Project 4: CSI-surfaced reports or signals elevated to a task for review/investigation.
UA_PROJECT_CSI = "UA: CSI Actions"
# Project 5: 24-hour catch-all — user-created or agent-detected tasks to close quickly.
UA_PROJECT_IMMEDIATE = "UA: Immediate Queue"

# All project names in priority order.
ALL_UA_PROJECTS = [
    UA_PROJECT_MISSION,
    UA_PROJECT_MEMORY,
    UA_PROJECT_PROACTIVE,
    UA_PROJECT_CSI,
    UA_PROJECT_IMMEDIATE,
]

# Sections are shared across all 5 projects.
UA_SECTIONS = {
    "immediate": "Immediate",
    "scheduled": "Scheduled",
    "background": "Background",
    "recurring": "Recurring",
}

# Legacy aliases for backward compat (old heartbeat references).
AGENT_TASKS_PROJECT = UA_PROJECT_IMMEDIATE  # default for old callers
BRAINSTORM_PROJECT = UA_PROJECT_PROACTIVE

# Brainstorm pipeline sections (preserved on UA: Proactive Intelligence)
BRAINSTORM_SECTIONS = {
    "inbox": "Inbox",
    "triaging": "Triaging",
    "heartbeat_candidate": "Heartbeat Candidate",
    "approved": "Approved for Build",
    "in_implementation": "In Implementation",
    "parked": "Parked / Rejected",
}

# Keeping for legacy compat
AGENT_SECTIONS = UA_SECTIONS

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

# Maps friendly project key → constant name.
PROJECT_KEY_MAP: dict[str, str] = {
    "mission": UA_PROJECT_MISSION,
    "identity": UA_PROJECT_MISSION,
    "mission_identity": UA_PROJECT_MISSION,
    "memory": UA_PROJECT_MEMORY,
    "memory_insights": UA_PROJECT_MEMORY,
    "proactive": UA_PROJECT_PROACTIVE,
    "proactive_intelligence": UA_PROJECT_PROACTIVE,
    "brainstorm": UA_PROJECT_PROACTIVE,
    "heartbeat": UA_PROJECT_PROACTIVE,
    "csi": UA_PROJECT_CSI,
    "csi_actions": UA_PROJECT_CSI,
    "immediate": UA_PROJECT_IMMEDIATE,
    "immediate_queue": UA_PROJECT_IMMEDIATE,
    "default": UA_PROJECT_IMMEDIATE,
}


@dataclass
class UA5Taxonomy:
    """Holds all 5 UA project IDs and their section mappings."""
    project_ids: dict[str, str]   # project_name -> project_id
    section_ids: dict[str, dict[str, str]]  # project_name -> {section_key -> section_id}
    brainstorm_sections: dict[str, str]  # kept for proactive project brainstorm pipeline

    @property
    def agent_project_id(self) -> str:
        """Legacy compat: returns Immediate Queue ID."""
        return self.project_ids.get(UA_PROJECT_IMMEDIATE, "")

    @property
    def brainstorm_project_id(self) -> str:
        """Legacy compat: returns Proactive Intelligence ID."""
        return self.project_ids.get(UA_PROJECT_PROACTIVE, "")

    @property
    def agent_sections(self) -> dict[str, str]:
        """Legacy compat: returns Immediate Queue section IDs."""
        return self.section_ids.get(UA_PROJECT_IMMEDIATE, {})


# Keep the old dataclass around for type-checking in legacy callers.
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
                raise ValueError("TODOIST_API_TOKEN or TODOIST_API_KEY is required")
            if TodoistAPI is None:
                raise RuntimeError("todoist-api-python is not installed")
            self._api = TodoistAPI(token)
        else:
            self._api = api

        self._taxonomy: Optional[UA5Taxonomy] = None

    @property
    def api(self):
        return self._api

    def ensure_taxonomy(self) -> dict:
        """Idempotently create all 5 UA projects, sections, and labels."""

        try:
            projects = _collect_items(self.api.get_projects())
            project_ids: dict[str, str] = {}
            section_ids: dict[str, dict[str, str]] = {}

            fallback = projects[0] if projects else None

            for project_name in ALL_UA_PROJECTS:
                found = _find_by_name(projects, project_name)
                if not found:
                    try:
                        found = self.api.add_project(name=project_name)
                    except Exception:
                        found = fallback
                pid = str(_get_field(found, "id", "") or "")
                if not pid:
                    raise RuntimeError(f"Could not obtain project ID for '{project_name}'")
                project_ids[project_name] = pid

                # All projects share general UA_SECTIONS except the Proactive Intelligence one
                # which additionally carries the brainstorm pipeline sections.
                if project_name == UA_PROJECT_PROACTIVE:
                    section_ids[project_name] = self._ensure_sections(pid, BRAINSTORM_SECTIONS)
                else:
                    section_ids[project_name] = self._ensure_sections(pid, UA_SECTIONS)

            # Bootstrap labels
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
                        pass  # idempotent

            self._taxonomy = UA5Taxonomy(
                project_ids=project_ids,
                section_ids=section_ids,
                brainstorm_sections=section_ids.get(UA_PROJECT_PROACTIVE, {}),
            )

            return {
                "project_ids": project_ids,
                "section_ids": section_ids,
                "labels_created": labels_created,
            }
        except Exception:
            # Never crash callers (heartbeat). Surface empty-ish payload.
            return {
                "project_ids": {},
                "section_ids": {},
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
        project_key: str = "default",
    ) -> dict[str, Any]:
        """Create a task routed to the appropriate UA project.

        project_key: one of the keys in PROJECT_KEY_MAP (e.g. 'csi', 'mission', 'memory',
            'proactive', 'immediate'). Defaults to 'UA: Immediate Queue'.
        """
        taxonomy = self._get_taxonomy_or_bootstrap()
        project_name = PROJECT_KEY_MAP.get((project_key or "").strip().lower(), UA_PROJECT_IMMEDIATE)
        project_id = taxonomy.project_ids.get(project_name, taxonomy.agent_project_id)
        # For the proactive project use brainstorm sections; others use UA_SECTIONS.
        if project_name == UA_PROJECT_PROACTIVE:
            project_sections = taxonomy.brainstorm_sections
        else:
            project_sections = taxonomy.section_ids.get(project_name, {})
        section_id = project_sections.get(section.lower()) or project_sections.get("background")
        task_labels = set(labels or [])
        task_labels.add("agent-ready")
        if sub_agent:
            task_labels.add(f"sub-agent:{sub_agent}")
        api_priority = PRIORITY_TO_API.get(priority.lower(), 1)
        task = self.api.add_task(
            content=content,
            description=description,
            project_id=project_id,
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


    def get_pipeline_summary(self) -> dict[str, Any]:
        """Return per-project task counts across all 5 UA projects."""
        taxonomy = self._get_taxonomy_or_bootstrap()
        result: dict[str, Any] = {}
        for project_name, project_id in taxonomy.project_ids.items():
            all_tasks = self.get_all_tasks(project_id=project_id)
            result[project_name] = len(all_tasks)
        # Also include brainstorm section breakdown for the proactive project
        proactive_id = taxonomy.project_ids.get(UA_PROJECT_PROACTIVE, "")
        if proactive_id:
            proactive_tasks = self.get_all_tasks(project_id=proactive_id)
            reverse = {v: k for k, v in taxonomy.brainstorm_sections.items()}
            section_counts: dict[str, int] = {k: 0 for k in taxonomy.brainstorm_sections}
            for task in proactive_tasks:
                sid = str(task.get("section_id") or "")
                key = reverse.get(sid)
                if key:
                    section_counts[key] = section_counts.get(key, 0) + 1
            result[f"{UA_PROJECT_PROACTIVE}__sections"] = section_counts
        return result


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

    def _get_taxonomy_or_bootstrap(self) -> UA5Taxonomy:
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
