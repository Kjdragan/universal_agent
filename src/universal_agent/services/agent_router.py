"""
agent_router.py — Simone-First Orchestration Router.

All tasks route to Simone. She decides delegation via batch triage
during her heartbeat cycle, using her full capabilities, skills, MCPs,
and sub-agents to evaluate work.

Previously: deterministic keyword/label-based routing to CODIE/ATLAS VPs.
Decommissioned: qualify_agent(), qualify_agent_llm(), keyword/label heuristics.

Design principles:
  - Simone is the primary executor — she takes the next task and works it directly
  - VPs are overflow capacity — Atlas and Codie handle work when Simone delegates
  - Simone delegates, not the system — she evaluates the full queue and decides
  - No task is done without Simone's sign-off
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent identifiers (kept for downstream compatibility)
# ---------------------------------------------------------------------------
AGENT_SIMONE = "simone"
AGENT_CODER = "vp.coder.primary"
AGENT_GENERAL = "vp.general.primary"



# ---------------------------------------------------------------------------
# Simone-First Router
# ---------------------------------------------------------------------------

def route_all_to_simone(
    claimed_tasks: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """All tasks route to Simone. She decides delegation via batch triage.

    Each task dict is enriched with a ``_routing`` key containing:
      - agent_id: always "simone"
      - confidence: "orchestrator"
      - reason: human-readable explanation
      - should_delegate: always False (Simone decides delegation herself)

    Returns a dict keyed by agent_id → list of tasks for that agent.
    """
    for task in claimed_tasks:
        task["_routing"] = {
            "agent_id": AGENT_SIMONE,
            "confidence": "orchestrator",
            "reason": "Simone-first: all tasks route through primary orchestrator",
            "should_delegate": False,
        }
    return {AGENT_SIMONE: list(claimed_tasks)}


# ---------------------------------------------------------------------------
# Backward-compatible aliases
# ---------------------------------------------------------------------------

def route_claimed_tasks(
    claimed_tasks: list[dict[str, Any]],
    **_kwargs: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Backward-compatible wrapper — delegates to ``route_all_to_simone``.

    Accepts and ignores legacy kwargs (available_agents, etc.) for
    call-site compatibility.
    """
    return route_all_to_simone(claimed_tasks)


async def route_claimed_tasks_llm(
    claimed_tasks: list[dict[str, Any]],
    **_kwargs: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Backward-compatible async wrapper — delegates to ``route_all_to_simone``.

    The LLM-powered routing is no longer needed; Simone herself is the
    intelligent router. This async shim remains so existing callers
    don't break.
    """
    return route_all_to_simone(claimed_tasks)
