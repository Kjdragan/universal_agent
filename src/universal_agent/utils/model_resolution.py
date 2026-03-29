from __future__ import annotations

import os

ZAI_MODEL_MAP = {
    "haiku": "GLM-4.5-Air",
    "sonnet": "GLM-4.7",      # Recommended by Z.AI for routine tasks
    "opus": "GLM-5.1",        # Recommended by Z.AI for complex tasks
}

def resolve_model(tier: str = "sonnet") -> str:
    """
    Resolve the Anthropic API model identifier, considering Z.AI proxy emulation mappings.
    Defaults to the recommended Z.AI Coding Plan models.
    """
    if tier == "haiku":
        env_val = os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL")
    elif tier == "sonnet":
        env_val = os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL")
    else:
        env_val = os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL")
        
    resolved = (env_val or "").strip()
    return resolved if resolved else ZAI_MODEL_MAP.get(tier, ZAI_MODEL_MAP["sonnet"])

def resolve_haiku() -> str:
    return resolve_model("haiku")

def resolve_sonnet() -> str:
    return resolve_model("sonnet")

def resolve_opus() -> str:
    return resolve_model("opus")

def resolve_claude_code_model(default: str = "opus") -> str:
    """
    Resolve the model string passed to Claude Code / claude-agent-sdk.

    We default to the centralized resolution mapped via Z.AI
    """
    # Overriding to pure programmatic central resolution (ignoring stringly-typed env keys temporarily)
    return resolve_model(default)


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
