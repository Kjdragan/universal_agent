from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

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
# in production and works reliably. Do NOT remap the haiku tier to glm-5-turbo
# (or anything else); the operator has mandated glm-4.5-air for this tier.
ZAI_MODEL_MAP = {
    "haiku": "glm-4.5-air",     # OPERATOR-LOCKED — never change (see note above).
    "sonnet": "glm-5-turbo",    # Z.AI standard model.
    "opus": "glm-5.1",          # Z.AI flagship model (NOT glm-5-1 — dash breaks it).
}


def resolve_model(tier: str = "sonnet") -> str:
    """
    Resolve the Anthropic API model identifier, considering Z.AI proxy emulation mappings.

    Defaults to the recommended Z.AI Coding Plan models. The default tier
    is "sonnet" (not opus) — the global daemon default per operator
    decision; explicit high-tier work (deep report construction, research
    synthesis) still requests "opus" by name.
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


def resolve_goal_eval_model(cody_mode: str = "zai") -> str | None:
    """Model for the built-in Claude Code ``/goal`` completion evaluator.

    Claude Code's ``/goal`` loop judges the completion condition after every
    turn with its "small fast model" — which current Claude Code collapses
    onto ``ANTHROPIC_DEFAULT_HAIKU_MODEL`` (``ANTHROPIC_SMALL_FAST_MODEL`` is
    deprecated; ref https://code.claude.com/docs/en/model-config). On the Z.AI
    routing the haiku tier is operator-locked to ``glm-4.5-air`` — too weak to
    adjudicate demo-build acceptance conditions reliably.

    This resolves a STRONGER evaluator model (sonnet tier → ``glm-5-turbo``)
    to be injected into the ``/goal`` work-turn subprocess ENV ONLY (see
    ``vp/clients/claude_cli_client.py::_execute_cli_session``). The value is
    never written to ``os.environ`` and never mutates ``ZAI_MODEL_MAP`` — the
    global haiku operator-lock stays intact everywhere else. The override
    rides the ``ANTHROPIC_DEFAULT_HAIKU_MODEL`` knob *inside that one child
    process* because Claude Code exposes no separate small-fast-model lever
    (the legacy ``ANTHROPIC_SMALL_FAST_MODEL`` was folded into the haiku one).

    Returns ``None`` (no override → built-in haiku/small-fast evaluator) when:
      * ``cody_mode == "anthropic"`` — a Claude-Max session keeps the real
        Haiku evaluator; injecting a Z.AI model id into an api.anthropic.com
        session would break it.
      * ``UA_GOAL_EVAL_MODEL`` is set to an opt-out token
        (off/none/default/haiku/disable/disabled).

    Precedence: anthropic-mode short-circuit → ``UA_GOAL_EVAL_MODEL`` (explicit
    model id, or opt-out token) → default ``resolve_sonnet()`` (glm-5-turbo).
    """
    # Never inject a Z.AI model id into an Anthropic-Max (OAuth) session.
    if (cody_mode or "").strip().lower() == "anthropic":
        return None
    raw = (os.getenv("UA_GOAL_EVAL_MODEL") or "").strip()
    if not raw:
        return resolve_sonnet()  # default ON: glm-5-turbo for the ZAI /goal evaluator
    if raw.lower() in {"off", "none", "default", "haiku", "disable", "disabled"}:
        return None  # explicit opt-out → built-in haiku/small-fast (glm-4.5-air)
    return raw  # explicit operator-pinned model id


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
#   - Haiku/Sonnet caps stay tight (a failed cheap-tier call is almost
#     always a 5xx loop that should fail fast so the dispatcher's retry
#     sweep can reopen the task).
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
    "haiku": 120.0,    # SDK preflight + tiny tasks; keep the cap tight so a failed cheap-tier call fails fast.
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


# ── Wire-id → limiter tier reverse map ────────────────────────────────────
# The ZAIRateLimiter buckets traffic by a small tier set. Given a WIRE-LEVEL
# model id (the string actually sent to the ZAI proxy), `model_id_to_tier`
# answers "which concurrency bucket does this belong to?". The tier set
# deliberately matches `model_call_timeout_seconds` (opus/sonnet/haiku) PLUS
# a new `mid` bucket for the Mission Control direct-model lane (glm-4.7) and
# the glm-4.6 literal — those bypass `ZAI_MODEL_MAP` and would otherwise be
# misbucketed.

# Models we resolve by literal id, independent of any env override. glm-4.7 is
# MISSION_CONTROL_DEFAULT_MODEL (bypasses ZAI_MODEL_MAP); glm-4.6 is a literal
# in csi_demo_triage_ranker.py.
_MID_TIER_LITERALS = frozenset({"glm-4.6", "glm-4.7"})

# Conservativeness order (lowest concurrency first). When several intent-env
# vars claim the SAME unknown id we resolve to the EARLIEST tier in this order.
_TIER_CONSERVATIVENESS = ("opus", "sonnet", "mid", "haiku")

# Process-level dedupe so the multi-env-claim conflict warning fires once per id.
_TIER_CONFLICT_WARNED: set[str] = set()

# Intent-expressing env vars → tier. Read at CALL time (these can be flipped at
# runtime). Only consulted for ids that match nothing wire-level above.
_ENV_TIER_VARS = (
    ("ANTHROPIC_DEFAULT_OPUS_MODEL", "opus"),
    ("ANTHROPIC_DEFAULT_SONNET_MODEL", "sonnet"),
    ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "haiku"),
    ("UA_MISSION_CONTROL_MODEL", "mid"),
)


def model_id_to_tier(model_id: str | None) -> str:
    """Map a WIRE-LEVEL model id to the limiter's tier bucket.

    Wire identity wins over env intent: an env var that names a model id
    expresses one caller's intent, but the SAME id can be sent by many other
    callers — letting an env var capture all of that id's traffic would invert
    the protection. So the precedence is reverse-`ZAI_MODEL_MAP` → literals →
    env vars → safe default. Returns one of {opus, sonnet, mid, haiku}; the
    set deliberately matches `model_call_timeout_seconds` plus the new `mid`.

    Precedence:
      1. Exact reverse-`ZAI_MODEL_MAP` match (case-insensitive).
      2. Literals glm-4.6 / glm-4.7 → "mid".
      3. Env vars (read at call time), only for ids matched by nothing above.
         If multiple env vars claim the same unknown id, resolve to the most
         conservative (lowest-concurrency) tier and warn once per process.
      4. Unknown / empty / None → "sonnet" (safe default).
    """
    if not model_id or not model_id.strip():
        return "sonnet"
    norm = model_id.strip().lower()

    # 1. Reverse ZAI_MODEL_MAP (case-insensitive).
    for tier, mapped in ZAI_MODEL_MAP.items():
        if mapped.lower() == norm:
            return tier

    # 2. Literal mid-tier ids that bypass ZAI_MODEL_MAP.
    if norm in _MID_TIER_LITERALS:
        return "mid"

    # 3. Intent-env vars — only for ids unclaimed wire-level above.
    claimed: list[str] = []
    for env_name, tier in _ENV_TIER_VARS:
        val = (os.getenv(env_name) or "").strip().lower()
        if val and val == norm:
            claimed.append(tier)
    if claimed:
        if len(set(claimed)) > 1:
            chosen = min(claimed, key=lambda t: _TIER_CONSERVATIVENESS.index(t))
            if norm not in _TIER_CONFLICT_WARNED:
                _TIER_CONFLICT_WARNED.add(norm)
                logger.warning(
                    "model_id_to_tier: id %r claimed by multiple intent-env vars "
                    "(%s); resolving to most-conservative tier %r",
                    model_id, ", ".join(sorted(set(claimed))), chosen,
                )
            return chosen
        return claimed[0]

    # 4. Safe default.
    return "sonnet"


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
