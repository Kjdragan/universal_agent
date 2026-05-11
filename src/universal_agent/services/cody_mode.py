"""Hermes Phase E.1 — Cody execution-mode resolver.

Picks between ``"zai"`` (default — cheap, ZAI/GLM proxy) and
``"anthropic"`` (real Anthropic Max plan via OAuth) per task.

Resolution order:
    1. ``task.cody_mode`` — per-task override on ``task_hub_items``
    2. ``UA_CODY_DEFAULT_MODE`` env var — system-wide default
    3. ``"zai"`` — hard default

The resolved value flows through ``vp_dispatch_mission`` into
``vp_missions.payload_json["cody_mode"]`` so the VP worker (CLI or
SDK adapter) can scrub ANTHROPIC_* env vars or otherwise route to
Anthropic Max for high-stakes coding tasks.

Today only the CLI execution mode applies the toggle (E.2.a — see
``vp/clients/claude_cli_client.py:_build_cli_env``). SDK in-process
mode (E.2.b) is a planned follow-up; the existing SDK code path
ignores the value and operates on whatever runtime env it inherits.
"""

from __future__ import annotations

import os
from typing import Any, Literal

CodyMode = Literal["zai", "anthropic"]

_ENV_VAR = "UA_CODY_DEFAULT_MODE"
_VALID_MODES: set[str] = {"zai", "anthropic"}
_DEFAULT_MODE: CodyMode = "zai"


def _normalize(raw: Any) -> str:
    return str(raw or "").strip().lower()


def resolve_cody_mode(task: dict[str, Any] | None = None) -> CodyMode:
    """Return the effective Cody execution mode for a task.

    Args:
        task: A ``task_hub_items`` row (or compatible dict). May be
            ``None`` to short-circuit to env/default.

    Returns:
        Either ``"zai"`` or ``"anthropic"``.
    """
    if task:
        per_task = _normalize(task.get("cody_mode"))
        if per_task in _VALID_MODES:
            return per_task  # type: ignore[return-value]

    env_mode = _normalize(os.getenv(_ENV_VAR))
    if env_mode in _VALID_MODES:
        return env_mode  # type: ignore[return-value]

    return _DEFAULT_MODE


def resolve_from_payload(payload: dict[str, Any] | None) -> CodyMode:
    """Resolve directly from a mission payload that already carries cody_mode.

    Used downstream of dispatch (VP worker, CLI client) where the task
    row is no longer in scope but the resolved value lives in the
    mission's ``payload.cody_mode``.
    """
    if payload:
        mode = _normalize(payload.get("cody_mode"))
        if mode in _VALID_MODES:
            return mode  # type: ignore[return-value]
    env_mode = _normalize(os.getenv(_ENV_VAR))
    if env_mode in _VALID_MODES:
        return env_mode  # type: ignore[return-value]
    return _DEFAULT_MODE


__all__ = ["CodyMode", "resolve_cody_mode", "resolve_from_payload"]
