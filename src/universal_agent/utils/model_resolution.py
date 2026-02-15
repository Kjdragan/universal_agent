from __future__ import annotations

import os


def resolve_claude_code_model(default: str = "sonnet") -> str:
    """
    Resolve the model string passed to Claude Code / claude-agent-sdk.

    We default to the Claude Code alias "sonnet" to keep model choice stable and
    easy to toggle without baking provider-specific model IDs into UA.

    Override precedence:
    1) UA_CLAUDE_CODE_MODEL (recommended; typically: sonnet|opus|haiku)
    2) MODEL_NAME (legacy)
    3) default (sonnet)
    """

    return (
        (os.getenv("UA_CLAUDE_CODE_MODEL") or "").strip()
        or (os.getenv("MODEL_NAME") or "").strip()
        or (default or "sonnet").strip()
        or "sonnet"
    )

