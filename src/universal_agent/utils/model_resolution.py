from __future__ import annotations

import os

# Z.AI Anthropic-compatible endpoint model mappings
# (https://api.z.ai/api/anthropic). The /api/anthropic endpoint does NOT
# support all model codes; e.g. "glm-5.1" requires the newer /api/paas/v4
# endpoint or explicit ANTHROPIC_DEFAULT_*_MODEL env var configuration.
# Ref: https://docs.z.ai/scenario-example/develop-tools/claude.md
#
# ─────────────────────────────────────────────────────────────────────────────
# OPERATOR LOCK — the haiku tier maps to glm-4.5-air. DO NOT CHANGE THIS, EVER.
# ─────────────────────────────────────────────────────────────────────────────
# The haiku tier is intentionally pinned to glm-4.5-air. This is a standing
# operator decision (Kevin, 2026-06-05): glm-4.5-air has been tested repeatedly
# in production and it works. Any earlier note in the git history claiming the
# glm-4.5-air lane is "flaky" / "wedges" / caused the atom-poem incident is
# SUPERSEDED and must NOT be used to justify remapping haiku away from
# glm-4.5-air. If a future audit or agent is tempted to "fix" this back to
# glm-5-turbo (or anything else): don't. The operator has mandated glm-4.5-air
# for the haiku tier regardless of any such reasoning.
ZAI_MODEL_MAP = {
    "haiku": "glm-4.5-air",     # OPERATOR-LOCKED — never change (see note above).
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
    """Resolve the haiku-tier model — `glm-4.5-air` by default.

    The haiku tier is OPERATOR-LOCKED to glm-4.5-air (see the note on
    ``ZAI_MODEL_MAP``); do not remap it. Only the
    ``ANTHROPIC_DEFAULT_HAIKU_MODEL`` env var can override at runtime.
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


# ── Mission Control Intelligence dedicated model lane ────────────────────
# The Mission Control intelligence system (tier-0 annotations, tier-1 card
# discovery, tier-2 page synthesis, event-title template generation) runs
# on its OWN model lane to avoid consuming Opus/Sonnet concurrency budget.
#
# Default: glm-4.7 — a fast, high-concurrency Z.AI direct model identifier
# documented in docs/ZAI_OPENAI_COMPATIBLE_SETUP.md. Bypasses ZAI_MODEL_MAP
# entirely; the value is passed through to AsyncAnthropic(model=...) as-is.
#
# Documented fallback if the lane gets flaky: glm-5-turbo.
MISSION_CONTROL_DEFAULT_MODEL = "glm-4.7"
MISSION_CONTROL_FALLBACK_MODEL = "glm-5-turbo"


def resolve_mission_control_model() -> str:
    """Resolve the dedicated Mission Control intelligence-lane model.

    Precedence:
      1) UA_MISSION_CONTROL_MODEL env override
      2) MISSION_CONTROL_DEFAULT_MODEL (glm-4.7)

    Does NOT pass through ZAI_MODEL_MAP — Mission Control deliberately
    sidesteps the haiku/sonnet/opus tier collapse so its compute does
    not contend with the application's main agent calls.
    """
    override = (os.getenv("UA_MISSION_CONTROL_MODEL") or "").strip()
    return override or MISSION_CONTROL_DEFAULT_MODEL


def mission_control_call_timeout_seconds() -> float:
    """Wall-clock cap for a single Mission Control LLM call.

    Override via UA_MISSION_CONTROL_CALL_TIMEOUT_SECONDS. Default 180s —
    matches the existing Chief-of-Staff timeout. 0.0 disables the cap.
    """
    raw = os.getenv("UA_MISSION_CONTROL_CALL_TIMEOUT_SECONDS")
    if raw is not None:
        try:
            return max(0.0, float(raw.strip()))
        except ValueError:
            pass
    return 180.0


# ── Per-tier wall-clock timeouts ──────────────────────────────────────
# Operating principle: don't run timeouts on the knife's edge. A cron
# that takes 8 minutes instead of 5 isn't a bug — it's a multi-step
# workflow that picked up an extra paper, ran an extra tool call, or
# hit a slow upstream API. Minutes of headroom are cheap; broken
# nightly pipelines that silently park in NOT_ASSIGNED are expensive.
#
# Tier philosophy:
#   - Haiku/Sonnet caps stay tight (cheap-tier wedges are almost always
#     a 5xx loop that should fail fast so the dispatcher's retry sweep
#     can reopen the task).
#   - Opus default is generous: real opus work is research, synthesis,
#     and multi-tool reasoning. The old 300s cap was killing legitimate
#     work (e.g. paper_to_podcast_daily — 6 consecutive nightly runs
#     died at ~5min wall-clock for at least a week before this bump).
#   - Per-cron / per-request override via
#     ``GatewayRequest.metadata["turn_timeout_seconds"]`` (consumed in
#     ``execution_engine.py``) is the right knob for "this specific
#     workflow needs even more time" — set it generously per-job
#     instead of dragging the global default upward to fit the
#     slowest cron.
#
# All defaults overridable via UA_MODEL_TIMEOUT_<TIER>_SECONDS env vars
# (haiku, sonnet, opus). Setting any to 0 disables the cap for that
# tier. Final fallback to UA_PROCESS_TURN_TIMEOUT_SECONDS for older
# call sites that don't pass a tier.
_TIER_DEFAULT_TIMEOUTS = {
    "haiku": 120.0,    # SDK preflight + tiny tasks; failed glm-4.5-air calls used to wedge for ~365s.
    "sonnet": 180.0,   # Daily-driver tasks; comfortably long enough for multi-tool turns.
    "opus": 1800.0,    # Heavy work (research, multi-doc synthesis, long crons). Generous on purpose.
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
