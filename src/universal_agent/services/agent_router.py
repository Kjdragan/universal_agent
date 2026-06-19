"""Simone-First Orchestration Router.

All tasks route to Simone. She decides delegation via batch triage
during her heartbeat cycle, using her full capabilities, skills, MCPs,
and sub-agents to evaluate work.

Design principles:
  - Simone is the primary executor — she takes the next task and works it directly
  - Simone delegates, not the system — when a task genuinely needs a VP
    (Cody for coding missions, Atlas for research/intel), she dispatches it
    herself via the VP-orchestration path; this router never pre-assigns VPs
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Agent identifiers (kept for downstream compatibility)
# ---------------------------------------------------------------------------
AGENT_SIMONE = "simone"
AGENT_CODER = "vp.coder.primary"
AGENT_GENERAL = "vp.general.primary"
# HOMER — opportunistic SECOND general VP (capacity twin of ATLAS). It is never
# a *classification* target; the priority dispatcher resolves the generic
# AGENT_GENERAL pool decision to this concrete id only when ATLAS is full and
# CODIE is idle (see priority_dispatcher.py::_pick_general_target).
AGENT_GENERAL_SECONDARY = "vp.general.secondary"


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

