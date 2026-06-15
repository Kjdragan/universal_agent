"""Shared VP slot-accounting (extracted for the D3 priority dispatcher).

Single source of truth for the per-VP concurrency caps so the legacy ToDo
dispatch path (``todo_dispatch_service``), the standalone Atlas-direct lane
(``atlas_direct_dispatch``), and the new ``priority_dispatcher`` all import
the SAME counting logic instead of reaching into another module's private
``_`` helper.

This module holds NO routing policy — only "how many VP slots are in use and
what's the cap". It was lifted verbatim out of ``todo_dispatch_service`` to
give M2/M3 a clean import seam; behaviour is unchanged.
"""

from __future__ import annotations

import os
from typing import Any


def _env_positive_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default)) or default))
    except Exception:
        return default


def _vp_active_counts(active_assignments: list[dict[str, Any]] | None) -> tuple[int, int]:
    """Count active VP coder and general assignments. Returns ``(coder, general)``.

    Classifies by the canonical ``agent_id`` field via exact set membership
    (NOT substring) to avoid false positives from words like "encoder" or
    "atlassian".

    Known ids/aliases:
      - Coder:   "vp.coder.primary", "codie", "coder"
      - General: "vp.general.primary", "atlas"
    """
    from universal_agent.services.agent_router import AGENT_CODER, AGENT_GENERAL

    coder_patterns = {AGENT_CODER, "codie", "coder"}
    general_patterns = {AGENT_GENERAL, "atlas"}

    coder = 0
    general = 0
    for assignment in active_assignments or []:
        agent_id = str(assignment.get("agent_id") or "").strip().lower()
        if agent_id in coder_patterns:
            coder += 1
        elif agent_id in general_patterns:
            general += 1
    return coder, general


def _available_agents_for_llm_routing(
    active_assignments: list[dict[str, Any]] | None,
) -> frozenset[str]:
    """Return the set of agent ids with a free slot.

    Simone is always available (singleton heartbeat session, uncapped). A VP
    is available only if its active assignment count is below its env cap
    (``UA_MAX_CONCURRENT_VP_CODER`` default 1, ``UA_MAX_CONCURRENT_VP_GENERAL``
    default 2).
    """
    from universal_agent.services.agent_router import (
        AGENT_CODER,
        AGENT_GENERAL,
        AGENT_SIMONE,
    )

    active_coder, active_general = _vp_active_counts(active_assignments)
    max_coder = _env_positive_int("UA_MAX_CONCURRENT_VP_CODER", 1)
    max_general = _env_positive_int("UA_MAX_CONCURRENT_VP_GENERAL", 2)
    available = {AGENT_SIMONE}
    if active_coder < max_coder:
        available.add(AGENT_CODER)
    if active_general < max_general:
        available.add(AGENT_GENERAL)
    return frozenset(available)
