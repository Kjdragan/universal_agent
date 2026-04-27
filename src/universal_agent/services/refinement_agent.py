"""Refinement Agent — guides brainstorm tasks through progressive refinement stages.

This agent uses the Anthropic Claude API to analyze a brainstorm task at its
current refinement stage and recommend:
  1. Advancing to the next stage (with an enriched description)
  2. Asking clarifying questions (before advancing)
  3. Holding at the current stage (needs more context)

Refinement stages (from task_hub.py):
  raw_idea → interviewing → exploring → crystallizing → decomposing → actionable

Design follows the user's philosophy:
  - Python plumbing for deterministic stage transitions
  - LLM only for the reasoning/analysis part
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from universal_agent.utils.model_resolution import resolve_sonnet

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stage progression order
# ---------------------------------------------------------------------------

STAGE_ORDER = [
    "raw_idea",
    "interviewing",
    "exploring",
    "crystallizing",
    "decomposing",
    "actionable",
]

_STAGE_INDEX = {s: i for i, s in enumerate(STAGE_ORDER)}


def next_stage(current: str) -> str | None:
    """Return the next stage in the progression, or None if already terminal."""
    idx = _STAGE_INDEX.get(current)
    if idx is None or idx >= len(STAGE_ORDER) - 1:
        return None
    return STAGE_ORDER[idx + 1]


# ---------------------------------------------------------------------------
# Stage-specific prompt guidance
# ---------------------------------------------------------------------------

_STAGE_GUIDANCE = {
    "raw_idea": (
        "This is a brand-new, unrefined idea. Your job is to assess whether it has "
        "enough clarity to start exploring, or if we need to interview the user first. "
        "Focus on: Is the goal clear? Is the scope defined? Are there obvious unknowns?"
    ),
    "interviewing": (
        "We are gathering requirements. Identify the most important unknowns and "
        "generate 2-4 focused questions that would help move this idea forward. "
        "Questions should be specific and actionable, not vague."
    ),
    "exploring": (
        "We understand the basics. Now explore feasibility, alternatives, and potential "
        "challenges. Assess technical viability and suggest approaches."
    ),
    "crystallizing": (
        "Consolidate everything learned into a clear, actionable specification. "
        "Produce a concise summary of what should be built, key requirements, "
        "and any constraints."
    ),
    "decomposing": (
        "The spec is clear. Suggest 2-5 concrete subtasks that would implement this. "
        "Each subtask should be independently completable and ordered by dependency."
    ),
}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_REFINE_SYSTEM = """\
You are a brainstorm refinement specialist. You help evolve vague ideas into
concrete, actionable work items through a structured progression.

Current refinement stage: {current_stage}

Stage guidance:
{stage_guidance}

You MUST respond with ONLY a JSON object (no markdown fencing, no commentary):
{{
  "recommendation": "advance" | "question" | "hold",
  "next_stage": "{next_stage_name}",
  "reasoning": "1-2 sentence explanation",
  "questions": ["question1", "question2"],
  "enriched_description": "improved task description incorporating new insights",
  "suggested_subtasks": [{{"title": "...", "description": "...", "priority": 2}}]
}}

Rules:
- "recommendation": "advance" means the idea is ready for the next stage.
- "recommendation": "question" means we need answers before advancing.
  Include 1-4 questions in the "questions" array.
- "recommendation": "hold" means insufficient context to proceed.
- "questions" should be empty [] unless recommendation is "question".
- "suggested_subtasks" should be empty [] unless stage is "decomposing".
- "enriched_description" should always be provided — improve the description
  with any new insights even if not advancing.
"""

_REFINE_USER = """\
Task to refine:

Title: {title}
Description: {description}

Current Stage: {current_stage}
Refinement History: {history_summary}
Recent Comments: {comments_summary}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class RefinementError(Exception):
    """Raised when refinement fails."""


async def refine_with_llm(
    *,
    title: str,
    description: str = "",
    current_stage: str = "raw_idea",
    refinement_history: dict[str, Any] | None = None,
    comments: list[dict[str, Any]] | None = None,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Call Claude to analyze a brainstorm task and recommend next action.

    Returns a dict with keys:
    - recommendation: "advance" | "question" | "hold"
    - next_stage: the recommended next stage
    - reasoning: explanation
    - questions: list of clarifying questions (if any)
    - enriched_description: improved description
    - suggested_subtasks: list of subtask dicts (if decomposing)
    """
    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:
        raise RefinementError("anthropic package not installed") from exc

    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        raise RefinementError("No Anthropic API key available (ANTHROPIC_API_KEY)")

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url

    client = AsyncAnthropic(**client_kwargs)

    # Build context strings
    next_stg = next_stage(current_stage) or "actionable"
    stage_guidance = _STAGE_GUIDANCE.get(current_stage, "Analyze the task and recommend next steps.")

    history_summary = "None"
    if refinement_history:
        entries = []
        for ts, entry in sorted(refinement_history.items()):
            entries.append(f"  [{ts}] → {entry.get('stage', '?')}: {json.dumps(entry.get('context', {}))}")
        history_summary = "\n".join(entries[-5:])  # Last 5 entries

    comments_summary = "None"
    if comments:
        entries = []
        for c in comments[-5:]:  # Last 5 comments
            entries.append(f"  [{c.get('author', '?')}] {c.get('content', '')[:200]}")
        comments_summary = "\n".join(entries)

    system_msg = _REFINE_SYSTEM.format(
        current_stage=current_stage,
        stage_guidance=stage_guidance,
        next_stage_name=next_stg,
    )
    user_msg = _REFINE_USER.format(
        title=title,
        description=description or "(none)",
        current_stage=current_stage,
        history_summary=history_summary,
        comments_summary=comments_summary,
    )

    try:
        response = await client.messages.create(
            model=model or resolve_sonnet(),
            max_tokens=1024,
            system=system_msg,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as exc:
        error_str = str(exc).lower()
        is_rate_limit = "429" in error_str or "too many requests" in error_str or "overloaded" in error_str
        if is_rate_limit:
            try:
                import asyncio

                from universal_agent.services.capacity_governor import CapacityGovernor
                governor = CapacityGovernor.get_instance()
                asyncio.ensure_future(governor.report_rate_limit("refinement_agent", error=exc))
            except Exception:
                pass
        logger.error("LLM call failed during refinement: %s", exc)
        raise RefinementError(f"LLM call failed: {exc}") from exc

    # Extract text from response
    raw_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            raw_text += block.text

    # Parse JSON — strip possible markdown fencing
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse refinement JSON: %s — raw: %s", exc, cleaned[:200])
        raise RefinementError(f"Invalid JSON from LLM: {exc}") from exc

    if not isinstance(result, dict):
        raise RefinementError(f"Expected a dict, got: {type(result).__name__}")

    # Validate and normalize
    recommendation = str(result.get("recommendation", "hold"))
    if recommendation not in ("advance", "question", "hold"):
        recommendation = "hold"

    return {
        "recommendation": recommendation,
        "next_stage": str(result.get("next_stage", next_stg)),
        "reasoning": str(result.get("reasoning", "")),
        "questions": list(result.get("questions", [])),
        "enriched_description": str(result.get("enriched_description", description)),
        "suggested_subtasks": list(result.get("suggested_subtasks", [])),
    }
