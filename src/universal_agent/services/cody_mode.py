"""Hermes Phase E.1 — Cody execution-mode resolver.

Picks between ``"zai"`` (cheap, ZAI/GLM proxy) and ``"anthropic"``
(real Anthropic Max plan via OAuth) per task.

Resolution order (highest priority first):
    1. ``task.cody_mode`` — per-task override on ``task_hub_items``.
    2. **Per-VP DB setting** ``cody_default_mode:<vp_id>`` — operator
       pin for one specific VP via the dashboard (e.g. flip only CODIE
       to "zai" without touching ATLAS). Requires ``vp_id`` + ``conn``.
    3. Global DB setting ``cody_default_mode`` — operator-configurable
       via the dashboard tile; applies to every VP that has no per-VP
       pin. Persisted in ``task_hub_settings``.
    4. ``UA_CODY_DEFAULT_MODE`` env var — deploy-time override; kept for
       ops use but typically unset in normal operation.
    5. **Per-VP profile default** (``VpProfile.inference_mode``) when a
       ``vp_id`` is supplied — the agent defines its own inference
       backend. As of 2026-06-07 every VP profile — CODIE
       (``vp.coder.primary``) included — defaults to "zai" (Anthropic
       began API-billing the Claude-Code-via-Max SDK path; see
       ``vp/profiles.py``). This replaced the old VP-blind hardcoded
       "anthropic" default (2026-06-03) that silently forced ATLAS
       research/intel missions onto the Max plan and burned
       5-hour-window credits meant for coding.
    6. ``"zai"`` — hardcoded last-resort fallback
       (``_HARDCODED_FALLBACK_MODE``), used only when no ``vp_id`` is
       known (e.g. the demo ``cody_demo_task`` path).

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
# 2026-05-11 PM: flipped "zai" → "anthropic" per operator decision.
# 2026-06-07: flipped back "anthropic" → "zai" — Anthropic began API-billing
# the Claude-Code-via-Max SDK path, so Cody no longer runs on real Anthropic
# by default. Every Cody/VP path (missions AND the demo/no-vp fallback) now
# resolves to ZAI/GLM unless explicitly overridden (per-task cody_mode,
# per-VP / global DB setting, or UA_CODY_DEFAULT_MODE).
_HARDCODED_FALLBACK_MODE: CodyMode = "zai"


def _normalize(raw: Any) -> str:
    return str(raw or "").strip().lower()


def _vp_setting_key(vp_id: str) -> str:
    """DB-setting key for a per-VP mode pin (e.g. ``cody_default_mode:vp.coder.primary``)."""
    return f"{_DB_SETTING_KEY}:{_normalize(vp_id)}"


def _read_setting_mode(conn: Any, key: str) -> Optional[CodyMode]:
    """Read a validated mode from a ``task_hub_settings`` key, or ``None``.

    Defensive on every failure path — if anything goes wrong reading the
    setting, fall through rather than break dispatch.
    """
    if conn is None or not key:
        return None
    try:
        from universal_agent.task_hub import _get_setting

        record = _get_setting(conn, key, default={})
    except Exception:
        return None
    if not isinstance(record, dict):
        return None
    mode = _normalize(record.get("mode"))
    if mode in _VALID_MODES:
        return mode  # type: ignore[return-value]
    return None


def _resolve_db_setting(conn: Any) -> Optional[CodyMode]:
    """Look up the GLOBAL operator-configured default mode from task_hub_settings.

    Returns ``None`` when no setting has been written.
    """
    return _read_setting_mode(conn, _DB_SETTING_KEY)


def _resolve_vp_db_setting(conn: Any, vp_id: Optional[str]) -> Optional[CodyMode]:
    """Look up the PER-VP operator pin (``cody_default_mode:<vp_id>``), or ``None``."""
    if not vp_id:
        return None
    return _read_setting_mode(conn, _vp_setting_key(vp_id))


def _resolve_profile_default(vp_id: str) -> Optional[CodyMode]:
    """Return the per-VP default inference mode from its ``VpProfile``.

    The agent defines its own inference backend (``inference_mode`` on
    the profile). Returns ``None`` when the VP is unknown/disabled or
    anything goes wrong looking it up — callers then fall through to the
    hardcoded last-resort default. Import is lazy to avoid any import
    cycle between the resolver and the VP profile registry.
    """
    vp_id = _normalize(vp_id)
    if not vp_id:
        return None
    try:
        from universal_agent.vp.profiles import get_vp_profile

        profile = get_vp_profile(vp_id)
    except Exception:
        return None
    if profile is None:
        return None
    mode = _normalize(getattr(profile, "inference_mode", ""))
    if mode in _VALID_MODES:
        return mode  # type: ignore[return-value]
    return None


def resolve_cody_mode(
    task: dict[str, Any] | None = None,
    *,
    conn: Any = None,
    vp_id: Optional[str] = None,
) -> CodyMode:
    """Return the effective Cody/VP execution mode for a task.

    Args:
        task: A ``task_hub_items`` row (or compatible dict). May be
            ``None`` to short-circuit to setting/env/profile/default.
        conn: Optional sqlite3 connection to the activity DB for
            reading the operator-configured DB setting. When omitted,
            DB-setting resolution is skipped (env + profile + hardcoded
            default still apply).
        vp_id: The VP the mission is being dispatched to (e.g.
            ``"vp.coder.primary"`` / ``"vp.general.primary"``). When
            supplied, the VP's ``VpProfile.inference_mode`` is the
            default — so the agent, not the dispatching function,
            defines its inference backend. Omitting it preserves the
            legacy VP-blind hardcoded default (used by the demo path).

    Returns:
        Either ``"zai"`` or ``"anthropic"``.

    """
    if task:
        per_task = _normalize(task.get("cody_mode"))
        if per_task in _VALID_MODES:
            return per_task  # type: ignore[return-value]

    # Per-VP operator pin beats the global pin — lets an operator flip one
    # VP (e.g. CODIE → zai) without disturbing the others.
    vp_db_mode = _resolve_vp_db_setting(conn, vp_id)
    if vp_db_mode is not None:
        return vp_db_mode

    db_mode = _resolve_db_setting(conn)
    if db_mode is not None:
        return db_mode

    env_mode = _normalize(os.getenv(_ENV_VAR))
    if env_mode in _VALID_MODES:
        return env_mode  # type: ignore[return-value]

    # Per-VP profile default — the agent defines its own inference. Only
    # consulted when no per-task / per-VP / global / env override is set.
    if vp_id:
        profile_mode = _resolve_profile_default(vp_id)
        if profile_mode is not None:
            return profile_mode

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


def set_default_mode(
    conn: Any, mode: str, *, vp_id: Optional[str] = None, updated_by: str = "operator"
) -> CodyMode:
    """Persist an operator-configured mode override.

    With ``vp_id`` omitted, writes the GLOBAL default under the
    ``cody_default_mode`` key. With ``vp_id`` set, writes a PER-VP pin
    under ``cody_default_mode:<vp_id>`` so one VP can be flipped without
    affecting the others. Raises ``ValueError`` for invalid mode values
    so callers (the settings endpoint) can surface a 400.

    Pass ``mode="clear"`` together with a ``vp_id`` to REMOVE that VP's
    per-VP pin (reverting it to global/env/profile default).
    """
    from datetime import datetime, timezone

    from universal_agent.task_hub import _set_setting

    normalized = _normalize(mode)

    # Clearing a per-VP pin reverts the VP to the global/env/profile chain.
    if normalized == "clear" and vp_id:
        _set_setting(conn, _vp_setting_key(vp_id), {})
        return _resolve_profile_default(vp_id) or _HARDCODED_FALLBACK_MODE

    if normalized not in _VALID_MODES:
        raise ValueError(
            f"mode must be one of {sorted(_VALID_MODES)} (got {mode!r})"
        )
    record = {
        "mode": normalized,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": str(updated_by or "operator"),
    }
    key = _vp_setting_key(vp_id) if vp_id else _DB_SETTING_KEY
    _set_setting(conn, key, record)
    return normalized  # type: ignore[return-value]


def get_default_mode_state(conn: Any, *, vp_id: Optional[str] = None) -> dict[str, Any]:
    """Return the effective mode + audit fields, mirroring resolve precedence.

    Used by the dashboard tile. With ``vp_id`` omitted, reports the
    GLOBAL setting (db → env → hardcoded). With ``vp_id`` set, reports
    that VP's *effective* mode and where it comes from:
    ``db_setting_vp`` (per-VP pin) → ``db_setting_global`` → ``env_var``
    → ``profile_default``. ``vp_id`` is echoed back when supplied.
    """
    from universal_agent.task_hub import _get_setting

    # Per-VP pin (only when a vp_id is supplied).
    if vp_id and conn is not None:
        vp_record = _get_setting(conn, _vp_setting_key(vp_id), default={})
        if isinstance(vp_record, dict) and _normalize(vp_record.get("mode")) in _VALID_MODES:
            return {
                "mode": _normalize(vp_record.get("mode")),
                "updated_at": vp_record.get("updated_at") or "",
                "updated_by": vp_record.get("updated_by") or "",
                "source": "db_setting_vp",
                "vp_id": vp_id,
            }

    # Global pin.
    record = _get_setting(conn, _DB_SETTING_KEY, default={}) if conn is not None else {}
    if isinstance(record, dict) and _normalize(record.get("mode")) in _VALID_MODES:
        return {
            "mode": _normalize(record.get("mode")),
            "updated_at": record.get("updated_at") or "",
            "updated_by": record.get("updated_by") or "",
            "source": "db_setting_global" if vp_id else "db_setting",
            **({"vp_id": vp_id} if vp_id else {}),
        }

    env_mode = _normalize(os.getenv(_ENV_VAR))
    if env_mode in _VALID_MODES:
        return {
            "mode": env_mode,
            "updated_at": "",
            "updated_by": "",
            "source": "env_var",
            **({"vp_id": vp_id} if vp_id else {}),
        }

    # Per-VP profile default (agent-defined) when we know the VP.
    if vp_id:
        profile_mode = _resolve_profile_default(vp_id)
        if profile_mode is not None:
            return {
                "mode": profile_mode,
                "updated_at": "",
                "updated_by": "",
                "source": "profile_default",
                "vp_id": vp_id,
            }

    return {
        "mode": _HARDCODED_FALLBACK_MODE,
        "updated_at": "",
        "updated_by": "",
        "source": "hardcoded_default",
        **({"vp_id": vp_id} if vp_id else {}),
    }


def list_vp_mode_states(conn: Any) -> list[dict[str, Any]]:
    """Return effective mode state for every enabled VP (dashboard per-VP tiles).

    Each entry: ``{vp_id, display_name, mode, source, updated_at, updated_by}``.
    Defensive — returns ``[]`` if the VP registry can't be loaded.
    """
    try:
        from universal_agent.vp.profiles import resolve_vp_profiles

        profiles = resolve_vp_profiles()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for vp_id, profile in profiles.items():
        state = get_default_mode_state(conn, vp_id=vp_id)
        state["display_name"] = getattr(profile, "display_name", vp_id)
        state["profile_default"] = _normalize(getattr(profile, "inference_mode", ""))
        out.append(state)
    return out


__all__ = [
    "CodyMode",
    "resolve_cody_mode",
    "resolve_from_payload",
    "set_default_mode",
    "get_default_mode_state",
    "list_vp_mode_states",
]
