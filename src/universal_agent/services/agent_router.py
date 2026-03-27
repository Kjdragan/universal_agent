"""
agent_router.py — Multi-agent task qualification and routing.

Given a claimed Task Hub item, determines which agent is best qualified
to execute it based on the task's labels, project_key, source_kind,
and title/description intent.

Routing targets:
  - simone:  Default primary daemon (communication, coordination, triage)
  - vp.coder.primary (CODIE): Code changes, refactoring, debugging, builds
  - vp.general.primary (ATLAS): Deep research, content generation, exploration

Design principles:
  - LLM-powered classification for intelligent intent understanding
  - Deterministic heuristics as synchronous fallback when LLM unavailable
  - Python plumbing for validation, availability checks, batch operations
  - Fallback to Simone for anything not clearly code or research
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent identifiers
# ---------------------------------------------------------------------------
AGENT_SIMONE = "simone"
AGENT_CODER = "vp.coder.primary"
AGENT_GENERAL = "vp.general.primary"

ALL_AGENTS = frozenset({AGENT_SIMONE, AGENT_CODER, AGENT_GENERAL})

# ---------------------------------------------------------------------------
# Routing rules (label-based)
# ---------------------------------------------------------------------------

# Labels that unambiguously route to CODIE (VP Coder)
_CODER_LABELS = frozenset({
    "code",
    "coding",
    "coder",
    "refactor",
    "debug",
    "debugging",
    "build",
    "deploy",
    "deployment",
    "devops",
    "infrastructure",
    "bugfix",
    "bug",
    "ci",
    "cd",
    "ci/cd",
    "test",
    "testing",
    "implementation",
    "engineering",
    "backend",
    "frontend",
    "api",
    "database",
    "migration",
    "vp-coder",
    "codie",
})

# Labels that unambiguously route to ATLAS (VP General)
_GENERAL_LABELS = frozenset({
    "research",
    "deep-research",
    "explore",
    "exploration",
    "content",
    "writing",
    "report",
    "analysis",
    "competitive-analysis",
    "scout",
    "scouting",
    "market-research",
    "documentation",
    "freelance",
    "vp-general",
    "atlas",
})

# Labels that force Simone (primary daemon — communication, coordination)
_SIMONE_LABELS = frozenset({
    "simone",
    "communication",
    "email",
    "notification",
    "calendar",
    "schedule",
    "meeting",
    "personal",
    "memory",
    "reflection",
    "brainstorm",
    "coordination",
    "triage",
})

# ---------------------------------------------------------------------------
# Routing rules (keyword heuristics for title/description)
# ---------------------------------------------------------------------------

_CODER_KEYWORDS = re.compile(
    r"\b("
    r"refactor|debug|fix\s+bug|implement|build|deploy|code|coding|"
    r"PR\b|pull\s+request|commit|merge|test\s+(suite|case|coverage)|"
    r"API\s+(endpoint|route)|database|migration|schema|"
    r"CI/CD|pipeline|linter|lint|formatter|"
    r"typescript|python|javascript|react|next\.?js|"
    r"dockerfile|docker|kubernetes|terraform"
    r")\b",
    re.IGNORECASE,
)

_GENERAL_KEYWORDS = re.compile(
    r"\b("
    r"research|explore|investigate|analyze|analysis|"
    r"competitive\s+analysis|market\s+research|"
    r"write\s+(report|article|document|content)|"
    r"scout|scouting|deep\s+dive|"
    r"summarize|synthesis|literature\s+review"
    r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _is_routing_enabled() -> bool:
    """Check if multi-agent routing is enabled via feature flag."""
    raw = (os.getenv("UA_AGENT_ROUTING_ENABLED") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    # Default: disabled (Phase 3 opt-in)
    return False


def _get_enabled_agents() -> frozenset[str]:
    """Get the set of agent IDs that are available for routing."""
    # Check if VP agents are enabled
    try:
        from universal_agent.feature_flags import vp_enabled_ids
        enabled_vps = set(vp_enabled_ids())
    except Exception:
        enabled_vps = set()

    agents = {AGENT_SIMONE}  # Simone is always available
    if AGENT_CODER in enabled_vps:
        agents.add(AGENT_CODER)
    if AGENT_GENERAL in enabled_vps:
        agents.add(AGENT_GENERAL)

    return frozenset(agents)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def qualify_agent(
    task: dict[str, Any],
    *,
    available_agents: Optional[frozenset[str]] = None,
) -> dict[str, Any]:
    """Determine which agent should execute a task.

    Args:
        task: A Task Hub item dict with keys like title, labels, project_key, etc.
        available_agents: Optional override of which agents are available.

    Returns:
        Dict with:
          - agent_id: str — the qualified agent ID
          - confidence: str — "label", "keyword", or "default"
          - reason: str — human-readable explanation
          - should_delegate: bool — True if the task should be delegated
            to a VP agent instead of the primary daemon
    """
    if available_agents is None:
        available_agents = _get_enabled_agents()

    labels = set()
    raw_labels = task.get("labels") or []
    if isinstance(raw_labels, str):
        try:
            import json
            raw_labels = json.loads(raw_labels)
        except Exception:
            raw_labels = [raw_labels]
    for lbl in raw_labels:
        labels.add(str(lbl).strip().lower())

    title = str(task.get("title") or "").strip()
    description = str(task.get("description") or "").strip()
    project_key = str(task.get("project_key") or "").strip().lower()
    source_kind = str(task.get("source_kind") or "").strip().lower()

    text_for_keywords = f"{title} {description}"

    # --- Rule 1: Explicit label-based routing ---

    # Check Simone labels first (highest priority — she coordinates)
    if labels & _SIMONE_LABELS:
        return _result(
            AGENT_SIMONE,
            confidence="label",
            reason=f"Simone label match: {labels & _SIMONE_LABELS}",
        )

    # Check Coder labels
    if labels & _CODER_LABELS and AGENT_CODER in available_agents:
        return _result(
            AGENT_CODER,
            confidence="label",
            reason=f"Coder label match: {labels & _CODER_LABELS}",
        )

    # Check General labels
    if labels & _GENERAL_LABELS and AGENT_GENERAL in available_agents:
        return _result(
            AGENT_GENERAL,
            confidence="label",
            reason=f"General label match: {labels & _GENERAL_LABELS}",
        )

    # --- Rule 2: Project key routing ---
    if project_key in {"coding", "engineering", "devops"} and AGENT_CODER in available_agents:
        return _result(
            AGENT_CODER,
            confidence="label",
            reason=f"Coder project_key: {project_key}",
        )
    if project_key in {"research", "scouting", "content"} and AGENT_GENERAL in available_agents:
        return _result(
            AGENT_GENERAL,
            confidence="label",
            reason=f"General project_key: {project_key}",
        )

    # --- Rule 3: Keyword heuristics (title + description) ---
    coder_match = _CODER_KEYWORDS.search(text_for_keywords)
    general_match = _GENERAL_KEYWORDS.search(text_for_keywords)

    if coder_match and not general_match and AGENT_CODER in available_agents:
        return _result(
            AGENT_CODER,
            confidence="keyword",
            reason=f"Coder keyword: '{coder_match.group()}'",
        )
    if general_match and not coder_match and AGENT_GENERAL in available_agents:
        return _result(
            AGENT_GENERAL,
            confidence="keyword",
            reason=f"General keyword: '{general_match.group()}'",
        )

    # Both keywords match — ambiguous; Simone triages
    if coder_match and general_match:
        return _result(
            AGENT_SIMONE,
            confidence="default",
            reason="Ambiguous keywords (both coder and general matched); Simone triages",
        )

    # --- Rule 4: Source-kind routing ---
    if source_kind in {"csi", "signal"} and AGENT_GENERAL in available_agents:
        return _result(
            AGENT_GENERAL,
            confidence="keyword",
            reason=f"CSI/signal source routes to research agent",
        )

    # --- Default: Simone handles it ---
    return _result(
        AGENT_SIMONE,
        confidence="default",
        reason="No routing signal detected; Simone handles",
    )


def _result(
    agent_id: str,
    *,
    confidence: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "confidence": confidence,
        "reason": reason,
        "should_delegate": agent_id != AGENT_SIMONE,
    }

# ---------------------------------------------------------------------------
# Batch routing (for heartbeat dispatch)
# ---------------------------------------------------------------------------

def route_claimed_tasks(
    claimed_tasks: list[dict[str, Any]],
    *,
    available_agents: Optional[frozenset[str]] = None,
) -> dict[str, list[dict[str, Any]]]:
    """Route a batch of claimed tasks to their qualified agents (sync).

    Returns a dict keyed by agent_id → list of tasks for that agent.
    Each task dict is enriched with a '_routing' key containing the
    qualification result.

    Note: This uses deterministic heuristics. For LLM-powered routing,
    use ``route_claimed_tasks_llm()`` instead.
    """
    if not _is_routing_enabled():
        # Routing disabled — all tasks go to Simone
        for task in claimed_tasks:
            task["_routing"] = _result(
                AGENT_SIMONE,
                confidence="default",
                reason="Agent routing disabled (UA_AGENT_ROUTING_ENABLED=0)",
            )
        return {AGENT_SIMONE: list(claimed_tasks)}

    buckets: dict[str, list[dict[str, Any]]] = {}
    for task in claimed_tasks:
        routing = qualify_agent(task, available_agents=available_agents)
        task["_routing"] = routing
        agent_id = routing["agent_id"]
        buckets.setdefault(agent_id, []).append(task)

    return buckets


# ---------------------------------------------------------------------------
# LLM-powered routing (async)
# ---------------------------------------------------------------------------

async def qualify_agent_llm(
    task: dict[str, Any],
    *,
    available_agents: Optional[frozenset[str]] = None,
) -> dict[str, Any]:
    """Determine which agent should execute a task using LLM reasoning.

    Uses the LLM classifier for intelligent intent-based routing, with
    deterministic heuristics as fallback if the LLM call fails.

    Returns the same dict shape as ``qualify_agent()``.
    """
    if available_agents is None:
        available_agents = _get_enabled_agents()

    # Parse task fields
    labels = []
    raw_labels = task.get("labels") or []
    if isinstance(raw_labels, str):
        try:
            import json
            raw_labels = json.loads(raw_labels)
        except Exception:
            raw_labels = [raw_labels]
    labels = [str(lbl).strip().lower() for lbl in raw_labels]

    title = str(task.get("title") or "").strip()
    description = str(task.get("description") or "").strip()
    source_kind = str(task.get("source_kind") or "").strip()
    project_key = str(task.get("project_key") or "").strip()

    try:
        from universal_agent.services.llm_classifier import classify_agent_route

        result = await classify_agent_route(
            title=title,
            description=description,
            labels=labels,
            source_kind=source_kind,
            project_key=project_key,
            available_agents=available_agents,
        )

        return {
            "agent_id": result["agent_id"],
            "confidence": result["confidence"],
            "reason": result["reasoning"],
            "should_delegate": result["should_delegate"],
            "method": result["method"],
        }

    except Exception as exc:
        logger.warning("LLM routing failed, falling back to heuristics: %s", exc)
        result = qualify_agent(task, available_agents=available_agents)
        result["method"] = "heuristic_fallback"
        return result


async def route_claimed_tasks_llm(
    claimed_tasks: list[dict[str, Any]],
    *,
    available_agents: Optional[frozenset[str]] = None,
) -> dict[str, list[dict[str, Any]]]:
    """Route a batch of claimed tasks using LLM-powered classification.

    Returns a dict keyed by agent_id → list of tasks for that agent.
    Each task dict is enriched with a '_routing' key.
    """
    if not _is_routing_enabled():
        for task in claimed_tasks:
            task["_routing"] = _result(
                AGENT_SIMONE,
                confidence="default",
                reason="Agent routing disabled (UA_AGENT_ROUTING_ENABLED=0)",
            )
        return {AGENT_SIMONE: list(claimed_tasks)}

    buckets: dict[str, list[dict[str, Any]]] = {}
    for task in claimed_tasks:
        routing = await qualify_agent_llm(task, available_agents=available_agents)
        task["_routing"] = routing
        agent_id = routing["agent_id"]
        buckets.setdefault(agent_id, []).append(task)

    return buckets

