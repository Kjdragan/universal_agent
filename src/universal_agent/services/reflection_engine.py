"""
reflection_engine.py — Overnight autonomous work generator for idle agents.

When the Task Hub dispatch queue is empty and the agent would normally sleep,
this engine provides a "reflection" prompt that asks the agent to:
1. Review its memory and Kevin's goals/missions
2. Consider recent task completions and patterns
3. Generate new tasks, brainstorm ideas, or advance existing brainstorms
4. Optionally start working on the highest-priority self-generated task

All logic is pure Python — the LLM receives the formatted context and decides
what to do.  The engine itself never calls an LLM.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from universal_agent import task_hub

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_REFLECTION_START_HOUR = 22  # 10 PM
DEFAULT_REFLECTION_END_HOUR = 7     # 7 AM
DEFAULT_MAX_NIGHTLY_TASKS = 10      # Max tasks an agent can work on per night
DEFAULT_REFLECTION_COOLDOWN_MINUTES = 30  # Minimum gap between reflection runs

_NIGHTLY_TASK_COUNTER_KEY = "reflection_nightly_counter"


def _parse_int_env(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def is_reflection_hours(
    *,
    now: Optional[datetime] = None,
    start_hour: Optional[int] = None,
    end_hour: Optional[int] = None,
) -> bool:
    """Return True if the current time is within the overnight reflection window.

    Default window: 10 PM – 7 AM (local time or USER_TIMEZONE).
    """
    if now is None:
        tz_name = os.getenv("USER_TIMEZONE", "America/Chicago")
        try:
            import pytz
            tz = pytz.timezone(tz_name)
            now = datetime.now(tz)
        except Exception:
            now = datetime.now()

    start = start_hour if start_hour is not None else _parse_int_env(
        "UA_REFLECTION_START_HOUR", DEFAULT_REFLECTION_START_HOUR
    )
    end = end_hour if end_hour is not None else _parse_int_env(
        "UA_REFLECTION_END_HOUR", DEFAULT_REFLECTION_END_HOUR
    )

    hour = now.hour
    if start > end:
        # Crosses midnight: e.g. 22-7 means 22,23,0,1,2,3,4,5,6
        return hour >= start or hour < end
    else:
        return start <= hour < end


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


def _get_nightly_task_count(conn: sqlite3.Connection) -> int:
    """Get how many tasks the agent has worked on during the current night window."""
    task_hub.ensure_schema(conn)
    setting = task_hub._get_setting(conn, _NIGHTLY_TASK_COUNTER_KEY)
    if not setting:
        return 0
    # Check if counter is from tonight (same date boundary)
    counter_date = str(setting.get("date") or "")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if counter_date != today:
        return 0  # Reset for new night
    return int(setting.get("count") or 0)


def _increment_nightly_task_count(conn: sqlite3.Connection, increment: int = 1) -> int:
    """Increment and return the updated nightly task count."""
    task_hub.ensure_schema(conn)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    setting = task_hub._get_setting(conn, _NIGHTLY_TASK_COUNTER_KEY)
    if not setting or str(setting.get("date") or "") != today:
        new_count = increment
    else:
        new_count = int(setting.get("count") or 0) + increment
    task_hub._set_setting(conn, _NIGHTLY_TASK_COUNTER_KEY, {
        "date": today,
        "count": new_count,
    })
    return new_count


def has_nightly_budget(conn: sqlite3.Connection) -> bool:
    """Check if the agent still has budget for nightly autonomous work."""
    max_tasks = _parse_int_env("UA_REFLECTION_MAX_NIGHTLY_TASKS", DEFAULT_MAX_NIGHTLY_TASKS)
    current = _get_nightly_task_count(conn)
    return current < max_tasks


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
    """Assemble all context needed for a reflection prompt.

    Returns a dict with:
      - recent_completions: what was recently accomplished
      - stalled_brainstorms: brainstorms that need attention
      - open_task_count: how many tasks are already queued
      - memory_context: goals/missions from memory
      - nightly_task_count: how many nightly tasks already done
      - nightly_budget_remaining: how many more allowed
      - reflection_prompt_text: formatted prompt text for injection
    """
    recent = _get_recent_completions(conn, limit=8)
    stalled = _get_stalled_brainstorms(conn)
    open_count = _get_open_task_count(conn)
    memory_hits = _get_memory_context(workspace_dir) if workspace_dir else []
    nightly_count = _get_nightly_task_count(conn)
    max_nightly = _parse_int_env("UA_REFLECTION_MAX_NIGHTLY_TASKS", DEFAULT_MAX_NIGHTLY_TASKS)
    budget_remaining = max(0, max_nightly - nightly_count)

    prompt_text = _format_reflection_prompt(
        recent_completions=recent,
        stalled_brainstorms=stalled,
        open_task_count=open_count,
        memory_context=memory_hits,
        budget_remaining=budget_remaining,
    )

    return {
        "recent_completions": recent,
        "stalled_brainstorms": stalled,
        "open_task_count": open_count,
        "memory_context": memory_hits,
        "nightly_task_count": nightly_count,
        "nightly_budget_remaining": budget_remaining,
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
    """Format the reflection context into a prompt section for the agent."""
    lines: list[str] = [
        "## 🌙 Overnight Reflection Mode",
        "",
        "The Task Hub dispatch queue is currently empty. You are operating in",
        "**overnight autonomous reflection mode**. Your goal is to advance Kevin's",
        "missions and projects even when no explicit tasks are queued.",
        "",
        f"**Budget:** You may work on up to **{budget_remaining}** more tasks tonight.",
        f"**Currently queued:** {open_task_count} task(s) already in the Task Hub.",
        "",
    ]

    # Stalled brainstorms — high priority
    if stalled_brainstorms:
        lines.append("### ⚠️ Stalled Brainstorms (Advance These First)")
        lines.append("")
        for b in stalled_brainstorms:
            lines.append(
                f"- **{b.get('title', 'Untitled')}** (stage: `{b.get('refinement_stage', '?')}`, "
                f"last updated: {b.get('updated_at', '?')[:16]})"
            )
        lines.append("")
        lines.append("Consider advancing these brainstorms to the next refinement stage.")
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

    # Action instructions
    lines.extend([
        "### Autonomous Actions You May Take",
        "",
        "1. **Advance stalled brainstorms** — Call the refinement tools to move brainstorms forward",
        "2. **Generate new brainstorm tasks** — If you identify gaps, create new tasks with `trigger_type=brainstorm`",
        "3. **Research and documentation** — Investigate open questions, write findings to work products",
        "4. **System improvements** — Review health checks, fix minor issues, improve monitoring",
        "5. **Morning report preparation** — Summarize tonight's activity for the 7 AM report",
        "",
        "**Do NOT:**",
        "- Deploy to production",
        "- Delete data or files without explicit approval",
        "- Send emails to external parties",
        "- Make breaking API changes",
        "",
        "Record all work as Task Hub items so it appears in the morning report.",
    ])

    return "\n".join(lines)
