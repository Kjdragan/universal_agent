"""Auto Refinement-to-Decomposition Loop.

Background service that automatically advances brainstorm tasks through their
refinement stages and triggers decomposition when they're ready.

Design:
  - Called on each heartbeat tick (side-effect, not blocking)
  - Scans for brainstorm tasks at various stages
  - Uses `refine_with_llm()` for intelligent stage advancement
  - When a task reaches 'decomposing', auto-triggers `decompose_with_llm()`
  - When decomposition succeeds, advances to 'actionable'
  - Feature-gated by UA_AUTO_REFINEMENT_ENABLED

Architecture follows the "plumbing + reasoning" pattern:
  - Python drives: scanning, filtering, DB writes, stage transitions, dedup
  - LLM reasons: refinement analysis, decomposition into subtasks
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

AUTO_REFINEMENT_ENABLED = str(
    os.getenv("UA_AUTO_REFINEMENT_ENABLED", "0")
).strip().lower() in {"1", "true", "yes", "on"}

# Max tasks to refine per cycle (budget guard)
MAX_REFINEMENTS_PER_CYCLE = int(os.getenv("UA_AUTO_REFINEMENT_MAX_PER_CYCLE", "3"))

# Cooldown between refinement attempts on the same task (minutes)
REFINEMENT_COOLDOWN_MINUTES = int(os.getenv("UA_AUTO_REFINEMENT_COOLDOWN_MINUTES", "30"))

# Stages we auto-process. 'actionable' is terminal — no auto-processing.
AUTO_PROCESSABLE_STAGES = frozenset({
    "raw_idea",
    "interviewing",
    "exploring",
    "crystallizing",
    "decomposing",
})


# ── Internal state ───────────────────────────────────────────────────────────

# Track last refinement attempt per task_id to enforce cooldown
_last_refinement_attempt: dict[str, float] = {}


def _is_on_cooldown(task_id: str) -> bool:
    """Check if a task was recently refined (within cooldown window)."""
    last = _last_refinement_attempt.get(task_id, 0.0)
    return (time.time() - last) < (REFINEMENT_COOLDOWN_MINUTES * 60)


def _record_attempt(task_id: str) -> None:
    """Record that we attempted refinement for a task."""
    _last_refinement_attempt[task_id] = time.time()


def clear_cooldowns() -> None:
    """Clear all cooldown state (for tests)."""
    _last_refinement_attempt.clear()


# ── Candidate Discovery ──────────────────────────────────────────────────────

def find_refinement_candidates(
    conn: sqlite3.Connection,
    *,
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """Find brainstorm tasks eligible for auto-refinement.

    Returns tasks with:
      - trigger_type = 'brainstorm'
      - status = 'open' (not in-progress, completed, or parked)
      - refinement_stage in AUTO_PROCESSABLE_STAGES
      - Not on cooldown

    Results are ordered by priority (highest first), then created_at (oldest first).
    """
    from universal_agent import task_hub
    task_hub.ensure_schema(conn)

    stage_placeholders = ",".join("?" for _ in AUTO_PROCESSABLE_STAGES)
    rows = conn.execute(
        f"""
        SELECT *
        FROM task_hub_items
        WHERE trigger_type = 'brainstorm'
          AND status = 'open'
          AND refinement_stage IN ({stage_placeholders})
        ORDER BY priority ASC, created_at ASC
        LIMIT ?
        """,
        (*AUTO_PROCESSABLE_STAGES, max_results),
    ).fetchall()

    candidates = []
    for row in rows:
        item = dict(row)
        task_id = item.get("task_id", "")
        if not _is_on_cooldown(task_id):
            candidates.append(item)

    return candidates[:max_results]


# ── Refinement Execution ─────────────────────────────────────────────────────

async def refine_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run a single auto-refinement cycle on a brainstorm task.

    Steps:
      1. Read current state
      2. Call refine_with_llm() for stage analysis
      3. If recommendation is "advance":
         - If advancing TO "decomposing", advance stage first
         - If stage IS "decomposing", call decompose_with_llm()
         - Otherwise, advance to the recommended next stage
      4. If recommendation is "question", enqueue questions
      5. Record the attempt cooldown

    Returns a summary dict with action taken.
    """
    from universal_agent import task_hub
    from universal_agent.services.refinement_agent import refine_with_llm, next_stage
    from universal_agent.services.decomposition_agent import decompose_with_llm

    _record_attempt(task_id)

    item = task_hub.get_item(conn, task_id)
    if item is None:
        return {"task_id": task_id, "action": "skip", "reason": "not_found"}

    current_stage = item.get("refinement_stage") or "raw_idea"
    if current_stage not in AUTO_PROCESSABLE_STAGES:
        return {"task_id": task_id, "action": "skip", "reason": f"stage={current_stage}"}

    title = item.get("title", "")
    description = item.get("description", "")

    # ── Special case: 'decomposing' stage → auto-decompose ────────────────
    if current_stage == "decomposing":
        return await _auto_decompose(
            conn, task_id=task_id, title=title, description=description, dry_run=dry_run,
        )

    # ── Normal refinement stages ──────────────────────────────────────────
    ref_state = task_hub.get_refinement_state(conn, task_id)
    history = ref_state.get("refinement_history", {})
    comments = task_hub.list_comments(conn, task_id, limit=10)

    result = await refine_with_llm(
        title=title,
        description=description,
        current_stage=current_stage,
        refinement_history=history,
        comments=comments,
    )

    recommendation = result.get("recommendation", "hold")

    if dry_run:
        return {
            "task_id": task_id,
            "action": "dry_run",
            "recommendation": recommendation,
            "current_stage": current_stage,
            "result": result,
        }

    if recommendation == "advance":
        next_stg = result.get("next_stage") or next_stage(current_stage) or "actionable"
        task_hub.advance_refinement(
            conn, task_id=task_id, new_stage=next_stg,
            context_update={"reasoning": result.get("reasoning", ""), "auto": True},
        )
        # Record enriched description as a comment
        if result.get("enriched_description"):
            task_hub.add_comment(
                conn, task_id=task_id,
                content=f"[Auto-Refinement] Enriched: {result['enriched_description']}",
                author="auto_refinement_loop",
            )
        task_hub.record_notification(
            conn, task_id=task_id, event_key=f"auto_refined_to_{next_stg}",
        )
        logger.info(
            "🔄 Auto-refined task %s: %s → %s (reason: %s)",
            task_id, current_stage, next_stg, result.get("reasoning", "?"),
        )

        # If we just advanced INTO decomposing, immediately decompose
        if next_stg == "decomposing":
            return await _auto_decompose(
                conn, task_id=task_id, title=title,
                description=result.get("enriched_description", description),
                dry_run=False,
            )

        return {
            "task_id": task_id,
            "action": "advanced",
            "from_stage": current_stage,
            "to_stage": next_stg,
            "reasoning": result.get("reasoning", ""),
        }

    elif recommendation == "question":
        questions = result.get("questions", [])
        for q in questions:
            task_hub.enqueue_question(
                conn, task_id=task_id,
                question_text=str(q),
                channel="auto_refinement",
            )
        logger.info(
            "🔄 Auto-refinement asked %d questions for task %s",
            len(questions), task_id,
        )
        return {
            "task_id": task_id,
            "action": "questions",
            "count": len(questions),
            "questions": questions,
        }

    else:  # hold
        logger.debug("🔄 Auto-refinement holding task %s at %s", task_id, current_stage)
        return {
            "task_id": task_id,
            "action": "hold",
            "current_stage": current_stage,
            "reasoning": result.get("reasoning", ""),
        }


async def _auto_decompose(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    title: str,
    description: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Auto-decompose a task and advance to actionable."""
    from universal_agent import task_hub
    from universal_agent.services.decomposition_agent import decompose_with_llm

    try:
        subtask_specs = await decompose_with_llm(
            title=title,
            description=description,
        )
    except Exception as exc:
        logger.warning("🔄 Auto-decomposition failed for %s: %s", task_id, exc)
        return {
            "task_id": task_id,
            "action": "decomposition_failed",
            "error": str(exc),
        }

    if dry_run:
        return {
            "task_id": task_id,
            "action": "dry_run_decompose",
            "subtask_count": len(subtask_specs),
            "subtasks": subtask_specs,
        }

    # Create subtasks
    created = task_hub.decompose_task(
        conn, parent_task_id=task_id, subtasks=subtask_specs,
    )

    # Advance to actionable
    task_hub.advance_refinement(
        conn, task_id=task_id, new_stage="actionable",
        context_update={
            "auto_decomposed": True,
            "subtask_count": len(created),
        },
    )

    task_hub.record_notification(
        conn, task_id=task_id, event_key="auto_decomposed",
    )

    logger.info(
        "🔄 Auto-decomposed task %s: %d subtasks created, advanced to actionable",
        task_id, len(created),
    )

    return {
        "task_id": task_id,
        "action": "decomposed",
        "subtask_count": len(created),
        "subtasks": [s.get("task_id") for s in created],
    }


# ── Batch Runner (called from heartbeat) ─────────────────────────────────────

async def run_auto_refinement_cycle(
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Run one auto-refinement cycle: find candidates and process them.

    This is designed to be called as a side-effect from the heartbeat tick.
    It's non-blocking (async) and budget-limited.

    Returns a summary of the cycle.
    """
    if not AUTO_REFINEMENT_ENABLED:
        return {"enabled": False, "processed": 0}

    # ── Capacity Governor check ──────────────────────────────────────────
    # Auto-refinement uses LLM calls — respect system-level capacity limits.
    try:
        from universal_agent.services.capacity_governor import CapacityGovernor
        _cap_ok, _cap_reason = CapacityGovernor.get_instance().can_dispatch()
        if not _cap_ok:
            logger.info("🔄 Auto-refinement deferred: %s", _cap_reason)
            return {"enabled": True, "processed": 0, "deferred": _cap_reason}
    except Exception:
        pass  # Governor unavailable — proceed without gating
    # ────────────────────────────────────────────────────────────────────

    candidates = find_refinement_candidates(
        conn, max_results=MAX_REFINEMENTS_PER_CYCLE,
    )

    if not candidates:
        return {"enabled": True, "processed": 0, "candidates": 0}

    results = []
    for item in candidates:
        task_id = item.get("task_id", "")
        try:
            result = await refine_task(conn, task_id)
            results.append(result)
        except Exception as exc:
            logger.warning("🔄 Auto-refinement error for %s: %s", task_id, exc)
            results.append({
                "task_id": task_id,
                "action": "error",
                "error": str(exc),
            })

    actions = [r.get("action", "unknown") for r in results]
    logger.info(
        "🔄 Auto-refinement cycle: processed=%d actions=%s",
        len(results), actions,
    )

    return {
        "enabled": True,
        "candidates": len(candidates),
        "processed": len(results),
        "results": results,
    }
