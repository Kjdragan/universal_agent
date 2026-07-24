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

# How far back "recent completions" reach for ideation pattern-awareness.
# Complements the vp_mission_failure exclusion (see `_get_recent_completions`):
# without a recency cap, resolved rescue items and other stale completions
# re-surface every heartbeat cycle. Operator-tunable via env without redeploying.
# Mirrors the freshness-window mechanism in
# ``services/invariants/operator_daily_mission_freshness.py``.
DEFAULT_RECENT_COMPLETION_HOURS = 6
_RECENT_COMPLETION_HOURS_ENV = "UA_REFLECTION_RECENT_COMPLETION_HOURS"


def _recent_completion_hours() -> int:
    """Resolved freshness window (hours) for recent completions, >=1."""
    return max(1, _parse_int_env(_RECENT_COMPLETION_HOURS_ENV, DEFAULT_RECENT_COMPLETION_HOURS))


def _parse_int_env(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


# ── Backpressure faucet (top-9 handoff, task 6) ─────────────────────────────
# When the held-proposal backlog is large AND the operator hasn't promoted or
# dismissed anything recently, every additional proposal just deepens an
# unread pile — the bottleneck is decision-throughput, not idea supply. The
# heartbeat consults this before building the ideation prompt (emit 0, log
# why); the morning report uses the same signal to switch to a ranked top-5
# drain view.
BACKPRESSURE_PENDING_THRESHOLD = _parse_int_env("UA_IDEATION_BACKPRESSURE_PENDING", 75)
BACKPRESSURE_STALL_DAYS = _parse_int_env("UA_IDEATION_BACKPRESSURE_STALL_DAYS", 5)


def _recent_review_activity_count(conn: sqlite3.Connection, days: int) -> int:
    """Count reflection rows that LEFT the held state within the window.

    A promote flips ``agent_ready`` to 1; a dismiss/park/complete moves
    ``status`` off ``open`` — either counts as operator review activity.
    """
    task_hub.ensure_schema(conn)
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM task_hub_items
        WHERE source_kind = 'reflection'
          AND (status != 'open' OR agent_ready = 1)
          AND updated_at >= datetime('now', ?)
        """,
        (f"-{int(days)} days",),
    ).fetchone()
    return int(row["c"]) if row else 0


def ideation_backpressure_reason(conn: sqlite3.Connection) -> Optional[str]:
    """Return why emission should pause, or None when the faucet may run.

    Pauses only when BOTH hold: pending held proposals exceed
    ``BACKPRESSURE_PENDING_THRESHOLD`` AND no reflection row left the held
    state within ``BACKPRESSURE_STALL_DAYS``. Any recent promote/dismiss
    re-opens the faucet immediately.
    """
    try:
        _, pending = _get_open_reflection_proposals(conn, limit=1)
        if pending <= BACKPRESSURE_PENDING_THRESHOLD:
            return None
        if _recent_review_activity_count(conn, BACKPRESSURE_STALL_DAYS) > 0:
            return None
        return (
            f"{pending} held proposals exceed the {BACKPRESSURE_PENDING_THRESHOLD} "
            f"backpressure threshold with no promote/dismiss activity in "
            f"{BACKPRESSURE_STALL_DAYS}d — pausing emission until reviews resume"
        )
    except sqlite3.Error:
        logger.debug("ideation_backpressure_reason: query failed", exc_info=True)
        return None  # fail-open: a probe error must not silence ideation


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
    """Get recently completed tasks for pattern analysis.

    Two independent gates (both required — see BRIEF):

    * Part A — exclude ``vp_mission_failure`` rows. Those are failure-rescue
      bookkeeping items (status completed/cancelled/parked), not meaningful
      accomplished work. Without this, the completed rescue items sat at the
      top of this list (they bump ``updated_at`` on every rescue) and rendered
      as ``- VP failure — <vp> (<mode>) (immediate)`` every heartbeat cycle —
      the ``(immediate)`` being the ``project_key`` default. This is the
      dedup-against-resolved-rescue-tasks gate; it fires on the rescue status
      regardless of age.
    * Part B — freshness window (``UA_REFLECTION_RECENT_COMPLETION_HOURS``,
      default 6h, mirroring
      ``services/invariants/operator_daily_mission_freshness.py``): bounds
      recency so stale completions of any kind stop re-surfacing.

    Note: an unhandled (status='open') failure is never read here — this query
    is the ideation pattern-awareness path, not the failure-alerting path.
    """
    task_hub.ensure_schema(conn)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=_recent_completion_hours())).isoformat()
    rows = conn.execute(
        """
        SELECT task_id, title, description, project_key, labels_json,
               created_at, updated_at
        FROM task_hub_items
        WHERE status = 'completed'
          AND source_kind != 'vp_mission_failure'
          AND updated_at >= ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (cutoff, limit),
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


def _get_open_reflection_proposals(
    conn: sqlite3.Connection, limit: int = 50
) -> tuple[list[dict[str, Any]], int]:
    """The ideator's OWN currently-held proposals, so it can self-dedup.

    Held reflection proposals (source_kind='reflection', open, not agent-ready)
    are exactly what the morning ideation report surfaces. Feeding them back into
    the ideation prompt is the highest-leverage fix for near-duplicate
    over-emission: without this the model never sees its own backlog, so it
    re-words the same idea every cycle and the insert-time lexical dedup
    (task_hub.normalize_reflection_dedup_key) — an exact-title match — cannot
    catch the paraphrases. Returns (most-recent proposals up to ``limit``, total open count).
    """
    task_hub.ensure_schema(conn)
    total_row = conn.execute(
        "SELECT COUNT(*) AS c FROM task_hub_items "
        "WHERE source_kind = 'reflection' AND status = 'open' AND agent_ready = 0"
    ).fetchone()
    total = int(total_row["c"]) if total_row else 0
    rows = conn.execute(
        """
        SELECT title, description
        FROM task_hub_items
        WHERE source_kind = 'reflection'
          AND status = 'open'
          AND agent_ready = 0
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows], total


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
    open_proposals, open_proposal_total = _get_open_reflection_proposals(conn)
    memory_hits = _get_memory_context(workspace_dir) if workspace_dir else []
    daily_count = get_daily_proactive_count(conn)
    remaining = get_budget_remaining(conn)

    prompt_text = _format_reflection_prompt(
        recent_completions=recent,
        stalled_brainstorms=stalled,
        open_task_count=open_count,
        open_proposals=open_proposals,
        open_proposal_total=open_proposal_total,
        memory_context=memory_hits,
        budget_remaining=remaining,
    )

    return {
        "recent_completions": recent,
        "stalled_brainstorms": stalled,
        "open_task_count": open_count,
        "open_proposals": open_proposals,
        "open_proposal_total": open_proposal_total,
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
    open_proposals: list[dict[str, Any]] | None = None,
    open_proposal_total: int = 0,
) -> str:
    """Format the ideation context into a prompt section for the agent."""
    open_proposals = open_proposals or []
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
        "to mind, or your idea is already covered by an open proposal below, create",
        "nothing** — silence is better than a near-duplicate.",
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

    # The ideator's OWN open proposals — so it self-dedups instead of re-wording.
    # This is the load-bearing anti-over-emission section: the model cannot avoid
    # proposing a near-duplicate of something it can't see.
    if open_proposals:
        lines.append("### 📋 Your Current Open Proposals — do NOT duplicate these")
        lines.append("")
        shown = len(open_proposals)
        if open_proposal_total > shown:
            lines.append(
                f"You already have **{open_proposal_total} proposals** awaiting Kevin's "
                f"review (the {shown} most recent are shown). The backlog is large — the bar "
                "for a genuinely new idea is high, and most themes are already covered."
            )
        else:
            lines.append(
                f"You already have **{open_proposal_total} proposal(s)** awaiting Kevin's review:"
            )
        lines.append("")
        for p in open_proposals:
            thesis = " ".join(str(p.get("description") or "").split())[:140]
            title = p.get("title") or "Untitled"
            lines.append(f"- **{title}** — {thesis}")
        lines.append("")
        lines.append(
            "**Before proposing, check this list AND the recent completions above.** "
            "If your idea — even reworded — is already an open proposal or was recently "
            "done, do NOT create a near-duplicate. Your options this cycle: (a) propose "
            "nothing (best when it's already covered), or (b) ONLY if you can materially "
            "advance or supersede a specific proposal above with new evidence or a sharper "
            "plan, name which one you are superseding and why it is better.",
        )
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
