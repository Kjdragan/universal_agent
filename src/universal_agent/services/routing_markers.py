"""Centralized routing markers for workflow classification.

Single source of truth for keyword-based routing heuristics used by
todo_dispatch_service and gateway_server. All markers use word-boundary
matching to reduce false positives (e.g. "report" won't match "airport").

Previously these were duplicated across modules with inconsistent matching:
some used raw substring (``"report" in text``), some used regex with \\b,
some used plain tuples. This module normalizes them into compiled patterns.
"""

from __future__ import annotations

import re
from typing import Sequence

# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _compile_markers(markers: Sequence[str]) -> re.Pattern[str]:
    """Compile marker strings into a single word-boundary regex.

    Multi-word markers like "code change" get internal ``\\s+`` instead of
    ``\\b``.  Single-word markers get ``\\b`` boundaries on both sides.
    """
    parts: list[str] = []
    for m in markers:
        normalized = m.strip()
        if not normalized:
            continue
        if " " in normalized:
            tokens = [re.escape(t) for t in normalized.split()]
            parts.append(r"\b" + r"\s+".join(tokens) + r"\b")
        else:
            parts.append(r"\b" + re.escape(normalized) + r"\b")
    return re.compile("|".join(parts), re.IGNORECASE)


# ---------------------------------------------------------------------------
# Workflow classification markers (todo_dispatch_service)
# ---------------------------------------------------------------------------

_CODE_WORKFLOW_TERMS: Sequence[str] = (
    "fix",
    "debug",
    "refactor",
    "implement",
    "code change",
    "update the code",
    "update code",
    "write code",
    "repository",
    "typescript",
    "javascript",
    "python",
    "unit test",
    "test failure",
    "api route",
)

_RESEARCH_WORKFLOW_TERMS: Sequence[str] = (
    "search for",
    "latest information",
    "latest developments",
    "research",
    "report",
    "analysis",
    "look up",
    "what happened",
    "pdf",
)

CODE_WORKFLOW_RE: re.Pattern[str] = _compile_markers(_CODE_WORKFLOW_TERMS)
RESEARCH_WORKFLOW_RE: re.Pattern[str] = _compile_markers(_RESEARCH_WORKFLOW_TERMS)

# Keep tuple forms for backward-compat callers that do ``any(m in text for m in ...)``
CODE_WORKFLOW_MARKERS: tuple[str, ...] = tuple(_CODE_WORKFLOW_TERMS)
RESEARCH_WORKFLOW_MARKERS: tuple[str, ...] = tuple(_RESEARCH_WORKFLOW_TERMS)


# ---------------------------------------------------------------------------
# CSI subtask role markers (gateway_server)
# ---------------------------------------------------------------------------

_CSI_CODE_TERMS: Sequence[str] = (
    "install", "fix", "patch", "signature", "hook", "code", "pydantic", "env",
)

_CSI_RESEARCH_TERMS: Sequence[str] = (
    "analyze", "investigate", "review", "assess",
)

_CSI_WRITER_TERMS: Sequence[str] = (
    "write", "draft", "publish", "message",
)

CSI_CODE_RE: re.Pattern[str] = _compile_markers(_CSI_CODE_TERMS)
CSI_RESEARCH_RE: re.Pattern[str] = _compile_markers(_CSI_RESEARCH_TERMS)
CSI_WRITER_RE: re.Pattern[str] = _compile_markers(_CSI_WRITER_TERMS)

CSI_CODE_SUBTASK_KEYWORDS: tuple[str, ...] = tuple(_CSI_CODE_TERMS)
CSI_RESEARCH_SUBTASK_KEYWORDS: tuple[str, ...] = tuple(_CSI_RESEARCH_TERMS)
CSI_WRITER_SUBTASK_KEYWORDS: tuple[str, ...] = tuple(_CSI_WRITER_TERMS)


# ---------------------------------------------------------------------------
# CSI recommendation ownership markers (gateway_server)
# ---------------------------------------------------------------------------

_CSI_AGENT_HINTS: Sequence[str] = (
    "install", "pip", "fix", "add", "update", "create", "implement",
    "patch", "refactor", "detect", "signature", "hook", "retry", "timeout",
    "script", "runbook", "automation", "cron", "pydantic", "env", "python",
    "code", "adapter",
)

_CSI_HUMAN_HINTS: Sequence[str] = (
    "manual", "human", "approval", "approve", "legal", "compliance",
    "budget", "meeting", "call", "email", "stakeholder", "sign-off",
    "sign off", "exec review", "kevin",
)

CSI_AGENT_HINTS_RE: re.Pattern[str] = _compile_markers(_CSI_AGENT_HINTS)
CSI_HUMAN_HINTS_RE: re.Pattern[str] = _compile_markers(_CSI_HUMAN_HINTS)

CSI_AGENT_RECOMMENDATION_HINTS: frozenset[str] = frozenset(_CSI_AGENT_HINTS)
CSI_HUMAN_RECOMMENDATION_HINTS: frozenset[str] = frozenset(_CSI_HUMAN_HINTS)
