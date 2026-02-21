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


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def resolve_agent_teams_enabled(default: bool = True) -> bool:
    """
    Resolve whether Claude Code Agent Teams should be enabled for the runtime.

    Precedence:
    1) UA_AGENT_TEAMS_ENABLED (UA-level override)
    2) CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS (native flag passthrough)
    3) default (True)
    """

    ua_override = (os.getenv("UA_AGENT_TEAMS_ENABLED") or "").strip()
    if ua_override:
        return _is_truthy(ua_override)

    native_flag = (os.getenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS") or "").strip()
    if native_flag:
        return _is_truthy(native_flag)

    return bool(default)
