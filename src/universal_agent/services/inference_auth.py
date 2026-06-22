"""Auth-mode env builder for inference run from a SCRIPT (outside the operator's
interactive session).

Why this exists: a subprocess (`claude -p`) or the Claude Agent SDK inherits the
env of whatever alias launched the session (``claudereal`` → Max OAuth, ``zai`` →
ZAI/GLM). A script that runs its own inference must therefore set the auth env it
INTENDS rather than rely on inheritance. See
``project_docs/06_platform/05_environments.md`` § "Running inference from a script".

This module is deliberately model-client-agnostic — it only shapes the env + the
model name. It is reusable by anything that runs the CLI / Agent SDK out of band,
not just the skill-description optimizer.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

AuthMode = Literal["anthropic", "zai", "auto"]

# The "opus" tier on ZAI currently maps to glm-5.2 (see
# 01_architecture/04_model_choice_and_resolution.md::ZAI_MODEL_MAP). The operator
# asked the ZAI fallback to use "opus → glm-5.2".
_ZAI_DEFAULT_MODEL = "glm-5.2"
_ANTHROPIC_DEFAULT_MODEL = "claude-opus-4-8"


def anthropic_subscription_available() -> bool:
    """True if a Max-subscription credential is reachable for the CLI/Agent SDK.

    The CLI/Agent-SDK path rides OAuth: a file session (``~/.claude/.credentials.json``)
    or a long-lived ``CLAUDE_CODE_OAUTH_TOKEN``. (The raw ``anthropic`` SDK cannot
    use either — that's the whole reason the optimizer's improve step broke.)
    """
    if os.getenv("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    return (Path.home() / ".claude" / ".credentials.json").exists()


def build_inference_env(mode: AuthMode) -> tuple[dict[str, str], str, str]:
    """Return ``(env, model, resolved_mode)`` for running CLI/Agent-SDK inference.

    - ``anthropic`` — strip every ``ANTHROPIC_*`` so the CLI/SDK falls through to
      Max OAuth (real Opus), no API key needed.
    - ``zai`` — inject UA's ZAI routing (``ANTHROPIC_BASE_URL`` + ``ANTHROPIC_AUTH_TOKEN``)
      and POP ``ANTHROPIC_API_KEY`` (the SDK errors when both api_key and auth_token
      are set; ZAI authenticates with the Bearer auth_token). Model = opus-tier GLM.
    - ``auto`` — ``anthropic`` if a subscription credential is reachable, else ``zai``.
    """
    if mode == "auto":
        mode = "anthropic" if anthropic_subscription_available() else "zai"

    if mode == "anthropic":
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("ANTHROPIC_") and k != "CLAUDECODE"}
        return env, _ANTHROPIC_DEFAULT_MODEL, "anthropic"

    if mode == "zai":
        # Inject ZAI routing the same way UA's runtime principals get it.
        from universal_agent.infisical_loader import initialize_runtime_secrets
        initialize_runtime_secrets()
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        env.pop("ANTHROPIC_API_KEY", None)  # ZAI uses Bearer auth_token; SDK rejects both-set
        model = env.get("ANTHROPIC_DEFAULT_OPUS_MODEL") or _ZAI_DEFAULT_MODEL
        return env, model, "zai"

    raise ValueError(f"unknown auth mode: {mode!r}")


def _demo() -> None:
    """Runnable self-check: anthropic mode must scrub ANTHROPIC_*; zai keeps a base."""
    os.environ["ANTHROPIC_API_KEY"] = "sentinel-should-be-scrubbed"
    env, model, resolved = build_inference_env("anthropic")
    assert resolved == "anthropic"
    assert model == _ANTHROPIC_DEFAULT_MODEL
    assert not any(k.startswith("ANTHROPIC_") for k in env), "anthropic mode must scrub ANTHROPIC_*"
    assert anthropic_subscription_available() in (True, False)  # never raises
    print("inference_auth self-check OK")


if __name__ == "__main__":
    _demo()
