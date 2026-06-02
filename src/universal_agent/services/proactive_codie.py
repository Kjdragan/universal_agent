"""Proactive CODIE cleanup and PR review helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
import re
import sqlite3
from typing import Any

from universal_agent import task_hub
from universal_agent.codebase_policy import (
    DEFAULT_APPROVED_CODEBASE_ROOT,
    approved_codebase_roots_from_env,
)
from universal_agent.services.proactive_artifacts import (
    ARTIFACT_STATUS_CANDIDATE,
    make_artifact_id,
    upsert_artifact,
)
from universal_agent.services.proactive_task_builder import queue_proactive_task

DEFAULT_CLEANUP_THEMES = (
    "add type hints to untyped public function signatures",
    "add or improve missing docstrings on public functions and classes",
    "extract magic strings and numeric literals into named constants",
    "improve error messages and logging context in except blocks",
    "add lightweight unit tests for under-tested helper functions",
    "standardize inconsistent import ordering and grouping",
    "replace bare except clauses with specific exception types",
)


def _resolve_default_codebase_root() -> str:
    """Resolve the codebase root to ship in proactive CODIE missions.

    Priority order:
      1. UA_PROACTIVE_CODIE_CODEBASE_ROOT — explicit override.
      2. First entry from UA_APPROVED_CODEBASE_ROOTS / production
         allowlist (`/opt/universal_agent` etc.).
      3. Hardcoded fallback to `DEFAULT_APPROVED_CODEBASE_ROOT`.

    The previous hardcoded value (`/home/kjdragan/lrepos/universal_agent`)
    was a developer-laptop path that doesn't exist on the production
    VPS. Production CODIE workers spawned, failed to access that path,
    and crashed in a tight restart loop — visible in production as 4+
    `vp.mission.started` events for a single mission_id and a flood
    of orphan-reconciled Task Hub items in the dashboard.
    """
    explicit = (os.getenv("UA_PROACTIVE_CODIE_CODEBASE_ROOT") or "").strip()
    if explicit:
        return explicit
    approved = approved_codebase_roots_from_env()
    if approved:
        return approved[0]
    return DEFAULT_APPROVED_CODEBASE_ROOT


CODIE_TARGET_AGENT = "vp.coder.primary"

_GITHUB_PR_RE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+")


def _pick_daily_theme() -> str:
    """Rotate through themes by day-of-year so each day covers a different focus."""
    day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
    return DEFAULT_CLEANUP_THEMES[day_of_year % len(DEFAULT_CLEANUP_THEMES)]


def queue_cleanup_task(
    conn: sqlite3.Connection,
    *,
    theme: str = "",
    note: str = "",
    priority: int = 2,
) -> dict[str, Any]:
    """Queue a review-gated CODIE code-quality work item in Task Hub."""
    task_hub.ensure_schema(conn)
    chosen_theme = str(theme or "").strip() or _pick_daily_theme()
    task_id = _cleanup_task_id(chosen_theme)
    preference_context = _preference_context(conn, task_type="codie_cleanup_task", topic_tags=["codie", "cleanup", chosen_theme])
    description = _cleanup_task_description(chosen_theme=chosen_theme, note=note, preference_context=preference_context)
    item = queue_proactive_task(
        conn,
        task_id=task_id,
        source_kind="proactive_codie",
        source_ref=_slug(chosen_theme),
        title=f"CODIE proactive cleanup: {chosen_theme}",
        description=description,
        priority=priority,
        labels=["agent-ready", "proactive-codie", "codie-cleanup", "code"],
        metadata={
            "source": "proactive_codie",
            "theme": chosen_theme,
            "review_gate": "pr_to_main",
            "complexity_target": "low_to_medium",
            "expected_work_product": "pull_request_to_main",
            "target_agent": CODIE_TARGET_AGENT,
            "codebase_root": _resolve_default_codebase_root(),
            "external_effect_policy": {
                "allow_pr": True,
                "allow_merge": False,
                "allow_main_push": False,
                "allow_deploy": False,
                # Hard constraints — CODIE must refuse, no exceptions.
                # Mirrors the explicit list in CODIE_SOUL.md.
                "allow_payments": False,
                "allow_public_communications": False,
                "allow_destructive_ops": False,
                "allow_secret_mutation": False,
                "allow_major_dep_bump": False,
                "allow_control_plane_edits": False,
            },
            "workflow_manifest": {
                "workflow_kind": "code_change",
                "delivery_mode": "interactive_chat",
                "requires_pdf": False,
                "final_channel": "chat",
                "canonical_executor": "simone_first",
                "target_agent": CODIE_TARGET_AGENT,
                "codebase_root": _resolve_default_codebase_root(),
                "repo_mutation_allowed": True,
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
    # The CC-Simone email directive is centralized in services/vp_email_directive
    # so all autonomous VP work uses the same template. Update the template
    # there to propagate changes here + every other VP producer.
    from universal_agent.services.vp_email_directive import (
        build_vp_outbound_email_directive,
    )

    email_directive = build_vp_outbound_email_directive(
        vp_id="vp.coder.primary",
        subject_prefix="[VP Status]",
        audience_hint="kevin",
    )

    lines = [
        "Cody should proactively improve code quality in the Universal Agent repository.",
        "",
        f"Code quality theme: {chosen_theme}",
        "",
        "Instructions:",
        "1. Inspect the Universal Agent repository for low-to-medium complexity cleanup work matching this theme. Prefer one coherent improvement area over broad sweeping changes.",
        "2. Keep the task non-breaking and reviewable. Do NOT redesign subsystems, change product behavior, alter deployment/config/secrets, or take on high-complexity migrations.",
        "3. Prefer simplification over expansion: delete dead code, reuse existing helpers, reduce brittle branching, and tighten tests before adding new abstractions.",
        "4. If a Claude Code simplify/cleanup skill is available in the runtime, use it as a focused helper for the chosen improvement area; do not let skill usage expand the scope.",
        "5. Branch hygiene (critical — read carefully). Create your per-task branch from a freshly-fetched, clean `origin/main` so it shares git history with the merge target: run `git fetch origin main` then `git checkout -B codie/<task> origin/main`. Do NOT branch from the local checkout's current HEAD — on the production tree that HEAD can be diverged/renamed, and branching off it produces a branch with no merge-base to `origin/main` (a disjoint-history PR showing hundreds of phantom files). All work goes on this `codie/<task>` branch.",
        "6. Use red-green TDD for behavior-touching changes: add or update a focused regression test, confirm it fails before the fix when practical, implement the smallest fix, then confirm the test passes.",
        "7. For mechanical-only cleanup where a failing regression is not meaningful, explain why red-green was not applicable and still run the focused test/lint/typecheck command that proves the cleanup is safe.",
        "8. Open a pull request targeting main for Kevin review. Before opening it, self-verify branch provenance and scope: `git merge-base origin/main HEAD` MUST return a commit (your branch is not disjoint from main) and `git diff --stat origin/main...HEAD` MUST list ONLY the files you intended to change. If there is no merge-base, or the diff shows dozens/hundreds of unrelated files, STOP — do not open the PR; instead report that the branch was cut from a diverged base and needs re-cutting from clean `origin/main` (per instruction 5). The changed-file count and commit count you put in the PR body must be derived from `origin/main...HEAD`, not from a raw `git log`. A PR is the required final work product unless no worthwhile improvement is found.",
        "9. Do not merge, push to main, deploy, delete production data, or make public releases.",
        "10. In the PR body, include rationale, changed files, tests run, red-green evidence or why it was not applicable, risks, rollback notes, and why the scope remained low/medium complexity.",
        "11. Before declaring done, write a COMPLETION.md file in your workspace summarizing what you produced relative to this brief, then send the outbound email as described in the directive below.",
        "12. The email body must contain a natural language summary explaining exactly what was proposed in the PR and why.",
        "13. If no worthwhile improvement is found, produce a short artifact explaining what was inspected and why no PR was warranted (still send the email so Kevin sees the no-op).",
        "14. Scope each PR to a single coherent improvement area. Do not bundle unrelated changes.",
        "",
        email_directive,
    ]
    extra = str(note or "").strip()
    if extra:
        lines.extend(["", f"Additional operator note: {extra}"])
    if preference_context:
        lines.extend(["", "Preference context:", preference_context])
    return "\n".join(lines)


def _preference_context(conn: sqlite3.Connection, *, task_type: str, topic_tags: list[str]) -> str:
    try:
        from universal_agent.services.proactive_preferences import (
            get_delegation_context,
        )

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
