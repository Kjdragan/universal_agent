"""Hermes Phase E.1 — Cody execution-mode resolver.

Picks between ``"zai"`` (cheap, ZAI/GLM proxy) and ``"anthropic"``
(real Anthropic Max plan via OAuth) per task.

Resolution order (highest priority first):
    1. ``task.cody_mode`` — per-task override on ``task_hub_items``.
    2. DB setting ``cody_default_mode`` — operator-configurable via the
       dashboard tile. Persisted in ``task_hub_settings``.
    3. ``UA_CODY_DEFAULT_MODE`` env var — deploy-time override; kept for
       ops use but typically unset in normal operation.
    4. ``"anthropic"`` — hardcoded fallback (flipped from "zai" on
       2026-05-11 PM per operator decision; Cody now defaults to running
       on real Anthropic Max unless explicitly overridden).

When the resolved mode is ``anthropic``, ``vp_dispatch_mission``
auto-routes the mission through ``execution_mode="cli"`` so the
spawned ``claude`` subprocess uses workspace-local OAuth (Anthropic
Max) instead of the gateway's ZAI-routed env. See
``vp/clients/claude_cli_client.py:_build_cli_env`` for the env-scrub
behavior.
"""

from __future__ import annotations

import os
from typing import Any, Literal, Optional

CodyMode = Literal["zai", "anthropic"]

_ENV_VAR = "UA_CODY_DEFAULT_MODE"
_DB_SETTING_KEY = "cody_default_mode"
_VALID_MODES: set[str] = {"zai", "anthropic"}
# 2026-05-11 PM: flipped from "zai" → "anthropic" per operator decision.
# Cody now runs on real Anthropic by default; operator can flip to "zai"
# via the dashboard UI when they want to save cost.
_HARDCODED_FALLBACK_MODE: CodyMode = "anthropic"


def _normalize(raw: Any) -> str:
    return str(raw or "").strip().lower()


def _resolve_db_setting(conn: Any) -> Optional[CodyMode]:
    """Look up the operator-configured default mode from task_hub_settings.

    Returns ``None`` when no setting has been written. Defensive on
    every failure path — if anything goes wrong reading the setting, we
    fall through to env / hardcoded default rather than break dispatch.
    """
    if conn is None:
        return None
    try:
        from universal_agent.task_hub import _get_setting

        record = _get_setting(conn, _DB_SETTING_KEY, default={})
    except Exception:
        return None
    if not isinstance(record, dict):
        return None
    mode = _normalize(record.get("mode"))
    if mode in _VALID_MODES:
        return mode  # type: ignore[return-value]
    return None


def resolve_cody_mode(
    task: dict[str, Any] | None = None,
    *,
    conn: Any = None,
) -> CodyMode:
    """Return the effective Cody execution mode for a task.

    Args:
        task: A ``task_hub_items`` row (or compatible dict). May be
            ``None`` to short-circuit to setting/env/default.
        conn: Optional sqlite3 connection to the activity DB for
            reading the operator-configured DB setting. When omitted,
            DB-setting resolution is skipped (env + hardcoded default
            still apply).

    Returns:
        Either ``"zai"`` or ``"anthropic"``.

    """
    if task:
        per_task = _normalize(task.get("cody_mode"))
        if per_task in _VALID_MODES:
            return per_task  # type: ignore[return-value]

    db_mode = _resolve_db_setting(conn)
    if db_mode is not None:
        return db_mode

    env_mode = _normalize(os.getenv(_ENV_VAR))
    if env_mode in _VALID_MODES:
        return env_mode  # type: ignore[return-value]

    return _HARDCODED_FALLBACK_MODE


def resolve_from_payload(payload: dict[str, Any] | None) -> CodyMode:
    """Resolve directly from a mission payload that already carries cody_mode.

    Used downstream of dispatch (VP worker, CLI client) where the task
    row is no longer in scope but the resolved value lives in the
    mission's ``payload.cody_mode`` / ``payload.metadata.cody_mode``.

    Does NOT consult the DB setting — by the time a mission payload is
    being executed the dispatch decision is already baked in. Falls
    through to env / hardcoded default only as defense.
    """
    if payload:
        mode = _normalize(payload.get("cody_mode"))
        if mode in _VALID_MODES:
            return mode  # type: ignore[return-value]
        # vp_orchestration plumbs cody_mode under metadata.cody_mode —
        # check there too for robustness.
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        if isinstance(metadata, dict):
            nested = _normalize(metadata.get("cody_mode"))
            if nested in _VALID_MODES:
                return nested  # type: ignore[return-value]
    env_mode = _normalize(os.getenv(_ENV_VAR))
    if env_mode in _VALID_MODES:
        return env_mode  # type: ignore[return-value]
    return _HARDCODED_FALLBACK_MODE


def set_default_mode(conn: Any, mode: str, *, updated_by: str = "operator") -> CodyMode:
    """Persist the operator-configured default Cody mode.

    Validates the input and writes a structured record to
    ``task_hub_settings`` under the ``cody_default_mode`` key. Raises
    ``ValueError`` for invalid mode values so callers (the settings
    endpoint) can surface a 400.
    """
    from datetime import datetime, timezone

    from universal_agent.task_hub import _set_setting

    normalized = _normalize(mode)
    if normalized not in _VALID_MODES:
        raise ValueError(
            f"mode must be one of {sorted(_VALID_MODES)} (got {mode!r})"
        )
    record = {
        "mode": normalized,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": str(updated_by or "operator"),
    }
    _set_setting(conn, _DB_SETTING_KEY, record)
    return normalized  # type: ignore[return-value]


def get_default_mode_state(conn: Any) -> dict[str, Any]:
    """Return the operator-configured default mode + audit fields.

    Used by the dashboard tile to show "current default + when changed
    + who changed it". When no setting has been written, returns the
    effective default (env or hardcoded) with empty audit fields so
    the UI can render "Default: anthropic (system default)" cleanly.
    """
    from universal_agent.task_hub import _get_setting

    record = _get_setting(conn, _DB_SETTING_KEY, default={}) if conn is not None else {}
    if isinstance(record, dict) and _normalize(record.get("mode")) in _VALID_MODES:
        return {
            "mode": _normalize(record.get("mode")),
            "updated_at": record.get("updated_at") or "",
            "updated_by": record.get("updated_by") or "",
            "source": "db_setting",
        }
    env_mode = _normalize(os.getenv(_ENV_VAR))
    if env_mode in _VALID_MODES:
        return {
            "mode": env_mode,
            "updated_at": "",
            "updated_by": "",
            "source": "env_var",
        }
    return {
        "mode": _HARDCODED_FALLBACK_MODE,
        "updated_at": "",
        "updated_by": "",
        "source": "hardcoded_default",
    }


__all__ = [
    "CodyMode",
    "resolve_cody_mode",
    "resolve_from_payload",
    "set_default_mode",
    "get_default_mode_state",
]
