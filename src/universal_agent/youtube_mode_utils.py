"""Shared YouTube learning-mode inference helpers."""

from __future__ import annotations

from typing import Final

MODE_EXPLAINER_ONLY: Final = "explainer_only"
MODE_EXPLAINER_PLUS_CODE: Final = "explainer_plus_code"

YOUTUBE_CODE_HINT_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
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
    }
)

YOUTUBE_NON_CODE_HINT_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
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
    }
)


def youtube_mode_tokens(*parts: object) -> str:
    return " ".join(str(part or "") for part in parts).strip().lower()


def infer_youtube_mode(*parts: object) -> str:
    tokens = youtube_mode_tokens(*parts)
    if not tokens:
        return MODE_EXPLAINER_ONLY
    has_code = any(keyword in tokens for keyword in YOUTUBE_CODE_HINT_KEYWORDS)
    has_non_code = any(keyword in tokens for keyword in YOUTUBE_NON_CODE_HINT_KEYWORDS)
    if has_non_code and not has_code:
        return MODE_EXPLAINER_ONLY
    return MODE_EXPLAINER_PLUS_CODE if has_code else MODE_EXPLAINER_ONLY


def youtube_probably_code(*parts: object) -> bool:
    return infer_youtube_mode(*parts) == MODE_EXPLAINER_PLUS_CODE


def youtube_explicitly_non_code(*parts: object) -> bool:
    tokens = youtube_mode_tokens(*parts)
    if not tokens:
        return False
    has_code = any(keyword in tokens for keyword in YOUTUBE_CODE_HINT_KEYWORDS)
    has_non_code = any(keyword in tokens for keyword in YOUTUBE_NON_CODE_HINT_KEYWORDS)
    return has_non_code and not has_code
