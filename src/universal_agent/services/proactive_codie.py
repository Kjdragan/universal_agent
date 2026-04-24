"""Proactive CODIE cleanup and PR review helpers."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from universal_agent import task_hub
from universal_agent.services.proactive_artifacts import (
    ARTIFACT_STATUS_CANDIDATE,
    make_artifact_id,
    upsert_artifact,
)

DEFAULT_CLEANUP_THEMES = (
    "reduce brittle routing heuristics",
    "delete dead code and stale compatibility layers",
    "add regression tests around fragile task lifecycle paths",
    "simplify duplicated prompt or dispatch plumbing",
    "improve documentation drift between code and canonical docs",
)

_GITHUB_PR_RE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+")


def queue_cleanup_task(
    conn: sqlite3.Connection,
    *,
    theme: str = "",
    note: str = "",
    priority: int = 2,
) -> dict[str, Any]:
    """Queue a review-gated CODIE cleanup work item in Task Hub."""
    task_hub.ensure_schema(conn)
    chosen_theme = str(theme or "").strip() or DEFAULT_CLEANUP_THEMES[0]
    task_id = _cleanup_task_id(chosen_theme)
    preference_context = _preference_context(conn, task_type="codie_cleanup_task", topic_tags=["codie", "cleanup", chosen_theme])
    description = _cleanup_task_description(chosen_theme=chosen_theme, note=note, preference_context=preference_context)
    item = task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "proactive_codie",
            "source_ref": _slug(chosen_theme),
            "title": f"CODIE proactive cleanup: {chosen_theme}",
            "description": description,
            "project_key": "proactive",
            "priority": max(1, min(int(priority or 2), 4)),
            "labels": ["agent-ready", "proactive-codie", "codie-cleanup", "code"],
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
            "trigger_type": "heartbeat_poll",
            "metadata": {
                "source": "proactive_codie",
                "theme": chosen_theme,
                "review_gate": "pr_to_develop",
                "external_effect_policy": {
                    "allow_pr": True,
                    "allow_merge": False,
                    "allow_main_push": False,
                    "allow_deploy": False,
                },
                "workflow_manifest": {
                    "workflow_kind": "code_change",
                    "delivery_mode": "interactive_chat",
                    "requires_pdf": False,
                    "final_channel": "chat",
                    "canonical_executor": "simone_first",
                    "repo_mutation_allowed": True,
                },
            },
        },
    )
    artifact = upsert_artifact(
        conn,
        artifact_type="codie_cleanup_task",
        source_kind="proactive_codie",
        source_ref=task_id,
        title=str(item.get("title") or ""),
        summary=f"Queued CODIE cleanup candidate for theme: {chosen_theme}",
        status=ARTIFACT_STATUS_CANDIDATE,
        priority=max(1, min(int(priority or 2), 4)),
        topic_tags=["codie", "cleanup", "universal-agent"],
        metadata={"task_id": task_id, "theme": chosen_theme},
    )
    return {"task": item, "artifact": artifact}


def register_pr_artifact(
    conn: sqlite3.Connection,
    *,
    pr_url: str,
    title: str,
    summary: str = "",
    branch: str = "",
    theme: str = "",
    tests: str = "",
    risk: str = "",
) -> dict[str, Any]:
    """Register a CODIE PR as a proactive review artifact."""
    clean_url = str(pr_url or "").strip()
    if not clean_url:
        raise ValueError("pr_url is required")
    metadata = {
        "pr_url": clean_url,
        "branch": str(branch or "").strip(),
        "theme": str(theme or "").strip(),
        "tests": str(tests or "").strip(),
        "risk": str(risk or "").strip(),
        "review_gate": "kevin_review_required_before_merge",
    }
    return upsert_artifact(
        conn,
        artifact_id=make_artifact_id(
            source_kind="codie_pr",
            source_ref=clean_url,
            artifact_type="codie_pr",
            title=title,
        ),
        artifact_type="codie_pr",
        source_kind="codie_pr",
        source_ref=clean_url,
        title=str(title or "").strip() or "CODIE proactive PR",
        summary=str(summary or "").strip() or "CODIE opened a proactive PR for review.",
        status=ARTIFACT_STATUS_CANDIDATE,
        priority=4,
        artifact_uri=clean_url,
        source_url=clean_url,
        topic_tags=["codie", "pull-request", "universal-agent"],
        metadata=metadata,
    )


def register_pr_artifact_from_text(
    conn: sqlite3.Connection,
    *,
    text: str,
    title: str = "",
    summary: str = "",
    theme: str = "",
) -> dict[str, Any] | None:
    """Register the first GitHub PR URL found in mission output text."""
    match = _GITHUB_PR_RE.search(str(text or ""))
    if not match:
        return None
    pr_url = match.group(0)
    return register_pr_artifact(
        conn,
        pr_url=pr_url,
        title=title or "CODIE proactive PR",
        summary=summary or "CODIE surfaced a PR for review.",
        theme=theme,
    )


def _cleanup_task_description(*, chosen_theme: str, note: str = "", preference_context: str = "") -> str:
    lines = [
        "CODIE should proactively improve the Universal Agent repository.",
        "",
        f"Cleanup theme: {chosen_theme}",
        "",
        "Instructions:",
        "1. Inspect the repository for low-hanging fruit matching this theme: find dead code, identify overcomplicated structures, and simplify them for efficiency.",
        "2. This is cleanup work only. Do NOT make any breaking code changes.",
        "3. Implement the change on a feature branch targeting develop.",
        "4. Add or update focused tests for the behavior touched.",
        "5. Open a pull request targeting develop for Kevin review (do not open as draft).",
        "6. Do not merge, push to main, deploy, delete production data, or make public releases.",
        "7. In the PR body, include rationale, changed files, tests run, risks, and rollback notes.",
        "8. After creating the PR, use the AgentMail tools from the shared VP mailbox (vp.agents@agentmail.to) to send an email to kevin.dragan@outlook.com.",
        "9. CC Simone's inbox (oddcity216@agentmail.to) on the email for situational awareness.",
        "10. Prefix the subject with '[VP Status]' and include this header at the top of the email body before your content:",
        "   '── VP Status Update (FYI — no action required) ──",
        "   This reply was sent by Codie (vp.coder.primary) directly to Kevin.",
        "   Simone is CC'd for situational awareness only. No action is needed from her.",
        "   ────────────────────────────────────────────────'",
        "11. The email must contain a natural language summary explaining exactly what was proposed in the PR and why.",
        "12. If no worthwhile improvement is found, produce a short artifact explaining what was inspected.",
    ]
    extra = str(note or "").strip()
    if extra:
        lines.extend(["", f"Additional operator note: {extra}"])
    if preference_context:
        lines.extend(["", "Preference context:", preference_context])
    return "\n".join(lines)


def _preference_context(conn: sqlite3.Connection, *, task_type: str, topic_tags: list[str]) -> str:
    try:
        from universal_agent.services.proactive_preferences import get_delegation_context

        return get_delegation_context(conn, task_type=task_type, topic_tags=topic_tags)
    except Exception:
        return ""


def _cleanup_task_id(theme: str) -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    digest = hashlib.sha256(f"{today}:{theme}".encode()).hexdigest()[:12]
    return f"proactive-codie:{digest}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug[:80] or "cleanup"
