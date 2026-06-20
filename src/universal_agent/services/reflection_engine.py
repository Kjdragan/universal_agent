"""reflection_engine.py — 24/7 autonomous ideation engine for idle agents.

When the Task Hub dispatch queue is empty and the agent would otherwise idle,
this engine provides an "ideation" prompt that asks the agent to propose ONE
high-value next action, grounded in memory / Kevin's goals, recent completions,
and stalled brainstorms.

IMPORTANT: The reflection engine is IDEATION-ONLY. Proposals are created as
`source_kind="reflection"` Task Hub items in a HOLDING state (agent_ready=False)
via `task_hub_create` — never auto-executed. They are surfaced to Kevin in the
morning ideation report, where a one-click "promote" flips them into the live
dispatch queue. (The ToDo Dispatch service then handles execution.)

All logic is pure Python — the LLM receives the formatted context and decides
what tasks to create.  The engine itself never calls an LLM.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
import sqlite3
from typing import Any, Optional

from universal_agent import task_hub
from universal_agent.services.proactive_budget import (
    DEFAULT_DAILY_BUDGET,
    get_budget_remaining,
    get_daily_proactive_count,
    has_daily_budget,
    increment_daily_proactive_count,
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
        "## 🧠 Autonomous Ideation Mode — one proposal this cycle",
        "",
        "The Task Hub dispatch queue is empty. Instead of idling, propose **one**",
        "high-value thing Kevin's team should take on next. This is a *proposal*, not",
        "work you execute: it is held for Kevin's review in tomorrow morning's ideation",
        "report, where he can promote it into the live queue with one click.",
        "",
        "Quality bar: one specific, non-obvious, well-reasoned proposal grounded in the",
        "context below beats five generic ones. **If nothing genuinely worthwhile comes",
        "to mind this cycle, create nothing** — silence is better than noise.",
        "",
        f"**Budget:** {budget_remaining} proposal(s) remaining today (paced ~1 per cycle).",
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
        "### How to record the proposal",
        "",
        "Call the **`task_hub_create`** tool exactly once with:",
        "- `title` — a crisp, specific headline (not a category).",
        "- `source_kind` = `\"reflection\"` — this routes it to the morning report and",
        "  holds it for review; it is never auto-executed.",
        "- `description` — use this exact structure so the report renders cleanly:",
        "",
        "  ```",
        "  **Rationale:** why this matters now / what it unblocks (2-3 sentences).",
        "  **First concrete step:** the very first action to take if approved.",
        "  **Effort:** S | M | L",
        "  **Suggested executor:** Atlas (research/synthesis) | Cody (code) | Simone | Kevin",
        "  ```",
        "",
        "Good sources: advancing a stalled brainstorm, a research/investigation tied to",
        "Kevin's missions, a system/quality improvement, a novel opportunity you noticed,",
        "or deepening a recent completion. Be a proactive colleague — pick what you would",
        "if it were your own product.",
        "",
        "**Do NOT** use `task_hub_task_action` to create — it only transitions existing",
        "tasks and will fail. **Do NOT** execute the idea, deploy, delete data, send",
        "external email, or make breaking changes — only record the one proposal.",
    ])

    return "\n".join(lines)
