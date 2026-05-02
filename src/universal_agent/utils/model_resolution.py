from __future__ import annotations

import os

# Z.AI Anthropic-compatible endpoint model mappings
# (https://api.z.ai/api/anthropic). The /api/anthropic endpoint does NOT
# support all model codes; e.g. "glm-5.1" requires the newer /api/paas/v4
# endpoint or explicit ANTHROPIC_DEFAULT_*_MODEL env var configuration.
# Ref: https://docs.z.ai/scenario-example/develop-tools/claude.md
#
# IMPORTANT — haiku tier is intentionally pointed at glm-5-turbo, not
# glm-4.5-air:
#   The Claude Agent SDK makes small INTERNAL preflight calls (system
#   prompt cache management, compaction routing, tool selection
#   classifier) using the haiku tier. The Z.AI proxy's glm-4.5-air
#   lane is empirically flaky — single requests can hang for ~6 min
#   then return 400 "Internal network failure" (code:1234). When that
#   preflight fails, the entire turn aborts before the main agent
#   model (glm-5.1 / glm-5-turbo) ever fires. Production trace from
#   the atom-poem stuck-task incident showed two consecutive 6-min
#   waits on glm-4.5-air preflight before a third attempt finally
#   succeeded — total 17 min of wall-clock loss for a 90-second job.
#
#   Mapping haiku → glm-5-turbo means the SDK's preflight uses the
#   same model the application uses for sonnet-tier work. Slightly
#   higher per-call cost ($0.012 → ~$0.04 estimated) but eliminates
#   the failure mode entirely. The user has explicitly opted out of
#   haiku as a default; only explicit haiku-requested code paths
#   would even notice (and we have none).
ZAI_MODEL_MAP = {
    "haiku": "glm-5-turbo",     # Was glm-4.5-air; remapped to dodge the flaky lane.
    "sonnet": "glm-5-turbo",    # Z.AI standard model.
    "opus": "glm-5.1",          # Z.AI flagship model (NOT glm-5-1 — dash breaks it).
}


def resolve_model(tier: str = "sonnet") -> str:
    """
    Resolve the Anthropic API model identifier, considering Z.AI proxy emulation mappings.

    Defaults to the recommended Z.AI Coding Plan models. The default tier
    is "sonnet" (not opus) — the global daemon default per the
    operational decision after the atom-poem incident; explicit
    high-tier work (deep report construction, research synthesis) still
    requests "opus" by name.
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
    """Resolve the haiku-tier model.

    Today this returns the same model as sonnet (`glm-5-turbo`) — see
    the rationale on `ZAI_MODEL_MAP`. Kept as a separate function so
    code that explicitly intends "the cheapest acceptable tier" still
    expresses intent, and so a future safe haiku lane can be re-enabled
    here without touching every call site.
    """
    return resolve_model("haiku")


def resolve_sonnet() -> str:
    """Resolve the sonnet-tier model — `glm-5-turbo` by default.

    Historically this was overridden to return opus. That override
    silently promoted every direct `resolve_sonnet()` caller to the
    expensive flagship model. The user's operational directive
    ("our global default model should be SONNET mapping to GLM-5-TURBO")
    restores honest behavior: sonnet means sonnet.
    """
    return resolve_model("sonnet")


def resolve_opus() -> str:
    """Resolve the opus-tier (flagship) model — `glm-5.1`."""
    return resolve_model("opus")


def resolve_claude_code_model(default: str = "sonnet") -> str:
    """
    Resolve the model string passed to Claude Code / claude-agent-sdk.

    Default tier is "sonnet" — the global daemon model. Callers that
    require flagship power (e.g., deep report construction) MUST pass
    `default="opus"` explicitly.
    """
    return resolve_model(default)


# ── Per-tier wall-clock timeouts ──────────────────────────────────────
# A single SDK turn can legitimately take a long time on opus (deep
# reports, multi-step research). On the cheap tiers a turn that takes
# 5+ minutes almost always means the upstream lane has wedged and is
# heading to a 400 error. Cap each tier separately so cheap-tier
# wedges fail fast and free up retry/escalation, while heavy opus
# work is allowed to breathe.
#
# All defaults overridable via UA_MODEL_TIMEOUT_<TIER>_SECONDS env vars
# (haiku, sonnet, opus). Setting any to 0 disables the cap for that
# tier. Final fallback to UA_PROCESS_TURN_TIMEOUT_SECONDS for older
# call sites that don't pass a tier.
_TIER_DEFAULT_TIMEOUTS = {
    "haiku": 120.0,    # SDK preflight + tiny tasks; failed glm-4.5-air calls used to wedge for ~365s.
    "sonnet": 180.0,   # Daily-driver tasks; comfortably long enough for multi-tool turns.
    "opus": 300.0,     # Heavy work (research, multi-doc synthesis); was effectively 6+ min before.
}


def model_call_timeout_seconds(tier: str = "sonnet") -> float:
    """Return the wall-clock cap (seconds) for a single SDK turn at the
    given tier. 0.0 disables the cap.
    """
    tier_norm = (tier or "sonnet").strip().lower()
    if tier_norm not in _TIER_DEFAULT_TIMEOUTS:
        tier_norm = "sonnet"
    env_name = f"UA_MODEL_TIMEOUT_{tier_norm.upper()}_SECONDS"
    raw = os.getenv(env_name)
    if raw is not None:
        try:
            value = float(raw.strip())
            return max(0.0, value)
        except ValueError:
            pass
    return _TIER_DEFAULT_TIMEOUTS[tier_norm]


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
