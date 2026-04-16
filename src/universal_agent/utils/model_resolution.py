from __future__ import annotations

import os

ZAI_MODEL_MAP = {
    "haiku": "glm-4.5-air",
    "sonnet": "glm-5-turbo",    # Maps to Z.AI standard model
    "opus": "glm-5.1",          # Maps to Z.AI advanced model
}

def resolve_model(tier: str = "opus") -> str:
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
    return resolved if resolved else ZAI_MODEL_MAP.get(tier, ZAI_MODEL_MAP["opus"])

def resolve_haiku() -> str:
    return resolve_model("haiku")

def resolve_sonnet() -> str:
    # Central configuration override: force Opus instead of Sonnet
    return resolve_model("opus")

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
