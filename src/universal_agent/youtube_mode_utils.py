"""Shared YouTube learning mode inference -- single source of truth.

Provides ``infer_youtube_mode`` which is used by both the CSI signals
ingest pipeline and the native YouTube playlist watcher to decide
whether a video should trigger code extraction (explainer_plus_code)
or text-only processing (explainer_only).
"""

from __future__ import annotations

_CODE_HINT_KEYWORDS = frozenset({
    "code",
    "coding",
    "programming",
    "python",
    "javascript",
    "typescript",
    "react",
    "nextjs",
    "next.js",
    "mcp",
    "api",
    "sdk",
    "cli",
    "sql",
    "database",
    "docker",
    "kubernetes",
    "repo",
    "github",
    "automation",
    "agent",
})

_NON_CODE_HINT_KEYWORDS = frozenset({
    "recipe",
    "cooking",
    "cook",
    "food",
    "kitchen",
    "grill",
    "charcoal",
    "souvlaki",
    "baking",
    "travel",
    "vlog",
    "music",
    "song",
    "workout",
    "fitness",
})

MODE_EXPLAINER_ONLY = "explainer_only"
MODE_EXPLAINER_PLUS_CODE = "explainer_plus_code"


def infer_youtube_mode(*parts: object) -> str:
    """Infer YouTube learning mode from arbitrary text parts.

    Joins all *parts* into a single lowercase string and checks for
    keyword hints.  Returns ``"explainer_plus_code"`` when code-related
    keywords are found (and no non-code keywords dominate), otherwise
    ``"explainer_only"``.
    """
    tokens = " ".join(str(part or "") for part in parts).strip().lower()
    if not tokens:
        return MODE_EXPLAINER_ONLY
    has_code = any(keyword in tokens for keyword in _CODE_HINT_KEYWORDS)
    has_non_code = any(keyword in tokens for keyword in _NON_CODE_HINT_KEYWORDS)
    if has_non_code and not has_code:
        return MODE_EXPLAINER_ONLY
    return MODE_EXPLAINER_PLUS_CODE if has_code else MODE_EXPLAINER_ONLY
