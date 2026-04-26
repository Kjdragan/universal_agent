"""
reflection_engine.py — 24/7 autonomous ideation engine for idle agents.

When the Task Hub dispatch queue is empty and the agent would otherwise idle,
this engine provides an "ideation" prompt that asks the agent to:
1. Review its memory and Kevin's goals/missions
2. Consider recent task completions and patterns
3. Generate new Task Hub items for autonomous execution

IMPORTANT: The reflection engine is IDEATION-ONLY.  It creates tasks in the
Task Hub — it does NOT execute them inline.  The ToDo Dispatch service handles
all execution to ensure session isolation and workspace integrity.

All logic is pure Python — the LLM receives the formatted context and decides
what tasks to create.  The engine itself never calls an LLM.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from universal_agent import task_hub
from universal_agent.services.proactive_budget import (
    get_daily_proactive_count,
    has_daily_budget,
    get_budget_remaining,
    increment_daily_proactive_count,
    DEFAULT_DAILY_BUDGET,
)

logger = logging.getLogger(__name__)

def _parse_int_env(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def is_reflection_enabled() -> bool:
    """Check if the reflection engine is enabled via feature flag."""
    raw = (os.getenv("UA_REFLECTION_ENABLED") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    # Default: enabled when autonomous heartbeat is also enabled
    if raw in {"1", "true", "yes", "on"}:
        return True
    # Fall through — follow UA_HEARTBEAT_AUTONOMOUS_ENABLED
    auto_raw = (os.getenv("UA_HEARTBEAT_AUTONOMOUS_ENABLED") or "").strip().lower()
    return auto_raw not in {"0", "false", "no", "off"}


# ---------------------------------------------------------------------------
# Context Builders
# ---------------------------------------------------------------------------

def _get_recent_completions(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    """Get recently completed tasks for pattern analysis."""
    task_hub.ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT task_id, title, description, project_key, labels_json,
               created_at, updated_at
        FROM task_hub_items
        WHERE status = 'completed'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_stalled_brainstorms(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Get brainstorm tasks that have stalled (not advanced in >24h)."""
    task_hub.ensure_schema(conn)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows = conn.execute(
        """
        SELECT task_id, title, refinement_stage, updated_at
        FROM task_hub_items
        WHERE status NOT IN ('completed', 'parked')
          AND refinement_stage IS NOT NULL
          AND refinement_stage != 'actionable'
          AND updated_at < ?
        ORDER BY updated_at ASC
        LIMIT 5
        """,
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_open_task_count(conn: sqlite3.Connection) -> int:
    """Count tasks currently open or in progress."""
    task_hub.ensure_schema(conn)
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM task_hub_items WHERE status IN ('open', 'in_progress', 'needs_review')"
    ).fetchone()
    return int(row["cnt"]) if row else 0


def _get_memory_context(workspace_dir: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search memory for goals, missions, and project context."""
    try:
        from universal_agent.memory.orchestrator import get_memory_orchestrator
        broker = get_memory_orchestrator(workspace_dir)
        # Search for goal-related memories
        queries = [
            "goals missions objectives priorities",
            "projects to work on next steps",
            "ideas improvements brainstorm",
        ]
        all_hits: list[dict[str, Any]] = []
        for q in queries:
            hits = broker.search(query=q, limit=3, direct_context=True)
            all_hits.extend(hits)
        # Deduplicate by snippet content
        seen_snippets: set[str] = set()
        unique_hits: list[dict[str, Any]] = []
        for hit in all_hits:
            snippet = str(hit.get("snippet") or hit.get("summary") or "")[:200]
            if snippet and snippet not in seen_snippets:
                seen_snippets.add(snippet)
                unique_hits.append(hit)
        return unique_hits[:limit]
    except Exception:
        logger.debug("Memory context unavailable for reflection", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Reflection Prompt Builder
# ---------------------------------------------------------------------------

def build_reflection_context(
    conn: sqlite3.Connection,
    *,
    workspace_dir: str = "",
) -> dict[str, Any]:
    """Assemble all context needed for an autonomous ideation prompt.

    Returns a dict with:
      - recent_completions: what was recently accomplished
      - stalled_brainstorms: brainstorms that need attention
      - open_task_count: how many tasks are already queued
      - memory_context: goals/missions from memory
      - nightly_task_count: daily proactive count (legacy key name)
      - nightly_budget_remaining: remaining daily budget (legacy key name)
      - reflection_prompt_text: formatted prompt text for injection
    """
    recent = _get_recent_completions(conn, limit=8)
    stalled = _get_stalled_brainstorms(conn)
    open_count = _get_open_task_count(conn)
    memory_hits = _get_memory_context(workspace_dir) if workspace_dir else []
    daily_count = get_daily_proactive_count(conn)
    remaining = get_budget_remaining(conn)

    prompt_text = _format_reflection_prompt(
        recent_completions=recent,
        stalled_brainstorms=stalled,
        open_task_count=open_count,
        memory_context=memory_hits,
        budget_remaining=remaining,
    )

    return {
        "recent_completions": recent,
        "stalled_brainstorms": stalled,
        "open_task_count": open_count,
        "memory_context": memory_hits,
        "nightly_task_count": daily_count,  # legacy key for heartbeat compat
        "nightly_budget_remaining": remaining,  # legacy key for heartbeat compat
        "reflection_prompt_text": prompt_text,
    }


def _format_reflection_prompt(
    *,
    recent_completions: list[dict[str, Any]],
    stalled_brainstorms: list[dict[str, Any]],
    open_task_count: int,
    memory_context: list[dict[str, Any]],
    budget_remaining: int,
) -> str:
    """Format the ideation context into a prompt section for the agent."""
    lines: list[str] = [
        "## 🧠 Autonomous Ideation Mode",
        "",
        "The Task Hub dispatch queue is currently empty. You are operating in",
        "**Autonomous Ideation Mode**. Your job is to think about what Kevin's",
        "team should work on next and create Task Hub items for autonomous execution.",
        "",
        f"**Daily Budget:** You may create up to **{budget_remaining}** more proactive tasks today.",
        f"**Currently queued:** {open_task_count} task(s) already in the Task Hub.",
        "",
    ]

    # Stalled brainstorms — high priority
    if stalled_brainstorms:
        lines.append("### ⚠️ Stalled Brainstorms (Consider Advancing)")
        lines.append("")
        for b in stalled_brainstorms:
            lines.append(
                f"- **{b.get('title', 'Untitled')}** (stage: `{b.get('refinement_stage', '?')}`, "
                f"last updated: {b.get('updated_at', '?')[:16]})"
            )
        lines.append("")
        lines.append("Consider creating tasks to advance these brainstorms.")
        lines.append("")

    # Recent completions — for pattern awareness
    if recent_completions:
        lines.append("### ✅ Recent Completions (Pattern Awareness)")
        lines.append("")
        for c in recent_completions[:5]:
            lines.append(f"- {c.get('title', 'Untitled')} ({c.get('project_key', 'immediate')})")
        lines.append("")

    # Memory context — goals/missions
    if memory_context:
        lines.append("### 🧠 Goals & Context from Memory")
        lines.append("")
        for hit in memory_context:
            snippet = str(hit.get("snippet") or hit.get("summary") or "")[:300]
            lines.append(f"- {snippet}")
        lines.append("")

    # Action instructions — IDEATION ONLY
    lines.extend([
        "### Your Role: Create Tasks, Don't Execute Them",
        "",
        "Use the `task_hub_task_action` tool to create new Task Hub items.",
        "The ToDo Dispatch service will handle all execution independently.",
        "",
        "Consider creating tasks for:",
        "1. **Advancing stalled brainstorms** — Create actionable tasks from brainstorm ideas",
        "2. **Research & investigation** — Topics aligned with Kevin's missions and goals",
        "3. **System improvements** — Monitoring, documentation, code quality",
        "4. **Novel exploration** — Something new and interesting you've noticed in the context",
        "5. **Follow-ups** — Continue work from recent completions that could benefit from more depth",
        "",
        "Use your judgment. Sometimes prioritize novelty. Sometimes follow up on",
        "stalled work. Sometimes create something entirely new based on what you",
        "know about Kevin's goals. Be a proactive colleague, not a passive executor.",
        "",
        "Set `source_kind` to `reflection` on all tasks you create.",
        "",
        "**Do NOT:**",
        "- Deploy to production",
        "- Delete data or files without explicit approval",
        "- Send emails to external parties",
        "- Make breaking API changes",
        "- Execute the tasks yourself — only create them",
    ])

    return "\n".join(lines)
