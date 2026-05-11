"""Centralized control plane for autonomous-loop on/off decisions.

UA has many autonomous loops — heartbeat tick, cron service registration,
dispatch sweep, AgentMail polling, VP event bridge, etc. Each historically
had its own ``UA_<NAME>_ENABLED`` env flag with its own default. Result:
local development required setting ~10 flags individually, and any new
loop added drift risk (operator forgets to add the new flag to their dev
``.env``, loop ticks in dev, burns ZAI quota / collides with prod).

This module centralizes the "should this loop run?" question:

* In **production** (``UA_RUNTIME_STAGE=production``) every loop defaults
  to its per-loop ``prod_default`` (today: usually ON).
* In **development** (``UA_RUNTIME_STAGE=development``) every loop
  defaults **OFF** — no individual flag needed. Spin up the gateway,
  routes work, loops don't tick, ZAI quota untouched.
* An explicit ``UA_<NAME>_ENABLED`` value always wins. Operators (or
  tests) who want a specific loop on in dev still set ``UA_FOO_ENABLED=1``
  in their ``.env`` and that loop ticks normally.

See: ``docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md``.
"""
from __future__ import annotations

import logging
import os
from typing import Final

logger = logging.getLogger(__name__)

_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
_FALSY: Final[frozenset[str]] = frozenset({"0", "false", "no", "off"})


def _normalize_flag_name(name: str) -> str:
    """Turn a loop name into the env-var suffix.

    ``"heartbeat_autonomous"`` → ``"HEARTBEAT_AUTONOMOUS"`` →
    full env var ``UA_HEARTBEAT_AUTONOMOUS_ENABLED``.
    """
    return name.strip().upper().replace("-", "_")


def is_development_runtime() -> bool:
    """True iff ``UA_RUNTIME_STAGE`` resolves to ``development``.

    Imported lazily to avoid circular-import surface during module init.
    Re-reads each call — runtime stage is set once at bootstrap and
    doesn't change, so this is cheap, but we don't memoize so tests
    can monkeypatch ``UA_RUNTIME_STAGE`` freely.
    """
    return (os.getenv("UA_RUNTIME_STAGE") or "").strip().lower() == "development"


def should_run_loop(name: str, *, prod_default: bool = True) -> bool:
    """Decide whether autonomous loop ``name`` should tick.

    Resolution order (first match wins):

    1. **Explicit override:** ``UA_<NAME>_ENABLED`` env var is set to a
       truthy or falsy value. Operator's word is final.
    2. **Dev default OFF:** ``UA_RUNTIME_STAGE=development`` and no
       explicit override → ``False``. Local dev never ticks loops by
       accident.
    3. **Prod default:** ``prod_default`` (caller's choice).

    Parameters
    ----------
    name : str
        Loop identifier in lowercase or uppercase. Will be normalized
        to the ``UA_<NAME>_ENABLED`` env-var convention.
    prod_default : bool, keyword-only
        What this loop should do in production when no explicit env
        flag is set. Most loops pass ``True`` (run by default in
        production); some opt-in loops pass ``False`` (require operator
        to set the flag even in production).

    Returns
    -------
    bool
        Whether the loop should run.
    """
    suffix = _normalize_flag_name(name)
    env_var = f"UA_{suffix}_ENABLED"
    raw = os.getenv(env_var)
    if raw is not None:
        normalized = raw.strip().lower()
        if normalized in _TRUTHY:
            return True
        if normalized in _FALSY:
            return False
        logger.warning(
            "Unrecognized value for %s=%r — treating as default", env_var, raw
        )

    if is_development_runtime():
        return False
    return prod_default


def explain_loop_decision(name: str, *, prod_default: bool = True) -> str:
    """Human-readable explanation of why ``should_run_loop`` returned what it did.

    Useful in startup logs so operators can grep ``loop_control:`` and see
    every gate decision at boot.
    """
    suffix = _normalize_flag_name(name)
    env_var = f"UA_{suffix}_ENABLED"
    raw = os.getenv(env_var)
    if raw is not None:
        normalized = raw.strip().lower()
        if normalized in _TRUTHY or normalized in _FALSY:
            decision = "ON" if normalized in _TRUTHY else "OFF"
            return f"{env_var}={raw} (explicit) → {decision}"
    if is_development_runtime():
        return f"{env_var} unset, UA_RUNTIME_STAGE=development → OFF (dev default)"
    decision = "ON" if prod_default else "OFF"
    return f"{env_var} unset, prod_default={prod_default} → {decision}"
