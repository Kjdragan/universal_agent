"""Declarative query-intent routing for the Universal Agent classifier."""

from __future__ import annotations

import os
from dataclasses import dataclass

# Canonical route constants
ROUTE_SIMPLE = "SIMPLE"
ROUTE_STANDARD = "STANDARD"
ROUTE_SYSTEM = "SYSTEM"


@dataclass(frozen=True)
class _Rule:
    """A single routing rule: keywords -> route."""

    route: str
    keywords: tuple[str, ...] = ()
    env_signals: tuple[str, ...] = ()
    requires_and_match: bool = False
    and_keywords: tuple[str, ...] = ()
    label: str = ""
    tier: str = ""


# ---------------------------------------------------------------------------
# Rule definitions — one place to edit, easy to audit
# ---------------------------------------------------------------------------

_SYSTEM_RULES: list[_Rule] = [
    _Rule(ROUTE_SYSTEM, keywords=("heartbeat_ok", "read heartbeat"), label="heartbeat_marker", tier="system"),
    _Rule(ROUTE_SYSTEM, keywords=("cron job", "scheduled task", "system task", "run cron", "cron check"), label="cron_marker", tier="system"),
    _Rule(ROUTE_SYSTEM, env_signals=("heartbeat", "cron", "system"), label="env_run_source", tier="system"),
]

_TOOL_REQUIRED_RULES: list[_Rule] = [
    _Rule(ROUTE_STANDARD, keywords=("[attached image:", "[attached ", "--- attached:"), label="attached_file", tier="tool_required"),
    _Rule(ROUTE_STANDARD, keywords=("search for", "send email", "email it", "email me", "email this", "run ", "execute ", "create a report"), label="tool_verb", tier="tool_required"),
    _Rule(ROUTE_STANDARD, keywords=("youtu.be", "youtube.com", "transcript", "get the transcript", "fetch transcript"), label="media_url", tier="tool_required"),
    _Rule(
        ROUTE_STANDARD,
        keywords=("get", "fetch", "scrape", "read", "summarize", "transcript", "content", "extract"),
        requires_and_match=True,
        and_keywords=("http://", "https://"),
        label="url_fetch",
        tier="tool_required",
    ),
]

_MEMORY_RULES: list[_Rule] = [
    _Rule(
        ROUTE_SIMPLE,
        keywords=(
            "please remember", "remember this", "my favorite", "my favourite",
            "my preferences", "my preference", "what are my", "what's my",
            "whats my", "when do i like", "do i like to",
            "my coding preferences", "my work environment", "my name is",
        ),
        label="memory_phrase",
        tier="memory",
    ),
]

_CONTEXT_ONLY_RULES: list[_Rule] = [
    _Rule(ROUTE_SIMPLE, keywords=("filename", "file name"), label="filename_reference", tier="context_only"),
]

# Evaluation order matters: system > tool-required > memory > context-only
_ALL_TIERS: list[tuple[list[_Rule], str]] = [
    (_SYSTEM_RULES, "system"),
    (_TOOL_REQUIRED_RULES, "tool_required"),
    (_MEMORY_RULES, "memory"),
    (_CONTEXT_ONLY_RULES, "context_only"),
]


def classify_heuristic(query: str) -> tuple[str, str, str]:
    """Return ``(route, matched_label, tier)`` using the declarative rule table.

    Returns ``("", "", "")`` when no heuristic matches (fall through to LLM).
    """
    lowered = query.lower()

    for rules, tier_name in _ALL_TIERS:
        for rule in rules:
            if rule.env_signals:
                run_source = os.getenv("UA_RUN_SOURCE", "").strip().lower()
                if run_source in rule.env_signals:
                    return rule.route, f"heuristic_{rule.label}", tier_name
                continue

            if rule.requires_and_match:
                if any(kw in lowered for kw in rule.and_keywords) and any(kw in lowered for kw in rule.keywords):
                    return rule.route, f"heuristic_{rule.label}", tier_name
                continue

            if any(kw in lowered for kw in rule.keywords):
                return rule.route, f"heuristic_{rule.label}", tier_name

    return "", "", ""


# Backward-compatible thin wrappers
def is_system_intent(query: str) -> bool:
    route, _, tier = classify_heuristic(query)
    return tier == "system"


def is_tool_required_intent(query: str) -> bool:
    route, _, tier = classify_heuristic(query)
    return tier == "tool_required"


def is_memory_intent(query: str) -> bool:
    route, _, tier = classify_heuristic(query)
    return tier == "memory"


def is_context_only_intent(query: str) -> bool:
    route, _, tier = classify_heuristic(query)
    return tier == "context_only"
