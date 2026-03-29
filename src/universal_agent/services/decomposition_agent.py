"""Decomposition Agent — breaks large tasks into actionable subtasks.

This agent uses the Anthropic Claude API to analyze a task and produce
a structured decomposition of 2-5 subtasks.  It is called by the
gateway decompose endpoint, NOT by the heartbeat loop.

Design follows the user's philosophy:
  - Python plumbing for deterministic structure
  - LLM only for the reasoning/planning part
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional
from universal_agent.utils.model_resolution import resolve_sonnet

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_DECOMPOSE_SYSTEM = """\
You are a task decomposition specialist.  Given a high-level task, break it
into 2-5 concrete, actionable subtasks.

Rules:
- Each subtask must be independently completable.
- Subtasks should be ordered by execution dependency (do-first items first).
- Assign a priority (1=low, 2=medium, 3=high) to each subtask.
- Keep titles concise (< 80 chars) and descriptions clear (1-3 sentences).
- Output ONLY a JSON array of objects, no markdown fencing, no commentary.

Each object:  {"title": "...", "description": "...", "priority": 2}
"""

_DECOMPOSE_USER = """\
Task to decompose:

Title: {title}
Description: {description}

Produce 2-5 subtasks as a JSON array.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class DecompositionError(Exception):
    """Raised when decomposition fails."""


async def decompose_with_llm(
    *,
    title: str,
    description: str = "",
    model: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Call Claude to decompose a task into subtasks.

    Returns a list of dicts like ``[{"title": "...", "description": "...", "priority": 2}]``.
    Raises :class:`DecompositionError` on parsing failure or API error.
    """
    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:
        raise DecompositionError("anthropic package not installed") from exc

    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        raise DecompositionError("No Anthropic API key available (ANTHROPIC_API_KEY)")

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url

    client = AsyncAnthropic(**client_kwargs)

    user_msg = _DECOMPOSE_USER.format(title=title, description=description or "(none)")

    try:
        response = await client.messages.create(
            model=model or resolve_sonnet(),
            max_tokens=1024,
            system=_DECOMPOSE_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as exc:
        error_str = str(exc).lower()
        is_rate_limit = "429" in error_str or "too many requests" in error_str or "overloaded" in error_str
        if is_rate_limit:
            try:
                from universal_agent.services.capacity_governor import CapacityGovernor
                import asyncio
                governor = CapacityGovernor.get_instance()
                asyncio.ensure_future(governor.report_rate_limit("decomposition_agent", error=exc))
            except Exception:
                pass
        logger.error("LLM call failed during decomposition: %s", exc)
        raise DecompositionError(f"LLM call failed: {exc}") from exc

    # Extract text from response
    raw_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            raw_text += block.text

    # Parse JSON — strip possible markdown fencing
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        # Remove fencing
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        subtasks = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse decomposition JSON: %s — raw: %s", exc, cleaned[:200])
        raise DecompositionError(f"Invalid JSON from LLM: {exc}") from exc

    if not isinstance(subtasks, list) or not subtasks:
        raise DecompositionError(f"Expected a non-empty list, got: {type(subtasks).__name__}")

    # Validate and normalize each subtask
    validated: list[dict[str, Any]] = []
    for i, sub in enumerate(subtasks[:5]):  # Cap at 5
        if not isinstance(sub, dict):
            continue
        validated.append({
            "title": str(sub.get("title", f"Sub-task {i + 1}"))[:120],
            "description": str(sub.get("description", "")),
            "priority": int(sub.get("priority", 2)),
        })

    if not validated:
        raise DecompositionError("LLM produced no valid subtasks")

    return validated
