"""Centralized control plane for autonomous-loop on/off decisions.

UA has many autonomous loops — heartbeat tick, cron service registration,
dispatch sweep, AgentMail polling, VP event bridge, etc. Each historically
had its own ``UA_<NAME>_ENABLED`` env flag with its own default. Result:
local development required setting ~10 flags individually, and any new
loop added drift risk (operator forgets to add the new flag to their dev
``.env``, loop ticks in dev, burns ZAI quota / collides with prod).

This module centralizes the "should this loop run?" question.

Resolution semantics
--------------------

**Production** (``UA_RUNTIME_STAGE`` != ``development``):

1. ``UA_<NAME>_ENABLED`` env var explicit value wins.
2. Else: ``prod_default`` (today: usually True).

**Development** (``UA_RUNTIME_STAGE=development``):

1. ``UA_DEV_<NAME>_FORCE_ON=1`` → True. This is the **dev-only operator
   opt-in**. Set this in your local ``.env`` when you specifically want
   to test a loop in dev. Distinct from ``UA_<NAME>_ENABLED`` (which is
   used in prod and may be injected by Infisical's ``development`` env
   as historical-prod-parity pollution).
2. ``UA_<NAME>_ENABLED=0/false/no/off`` → False. Operator explicitly
   asked for off; honor it.
3. Else: False. Dev defaults OFF for every loop, **ignoring** any
   truthy ``UA_<NAME>_ENABLED`` that Infisical may have injected. This
   is the defensive layer: even if Infisical's dev environment has
   ``UA_HEARTBEAT_ENABLED=1`` mirrored from prod, dev still runs clean.

The dev-default-OFF behavior was tightened on 2026-05-11 after a
desktop ``just dev`` verification showed heartbeat + cron service +
5 cron jobs firing despite Phase C.2 gates, because Infisical's
``development`` env injects ``UA_<NAME>_ENABLED=1`` values mirrored
from production. The first iteration of ``should_run_loop`` honored
those as explicit overrides — which is correct semantics for an
operator-set env var but wrong for Infisical injection.

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


def _env_truthy(env_var: str) -> bool:
    raw = os.getenv(env_var)
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY


def _env_falsy(env_var: str) -> bool:
    raw = os.getenv(env_var)
    if raw is None:
        return False
    return raw.strip().lower() in _FALSY


def should_run_loop(name: str, *, prod_default: bool = True) -> bool:
    """Decide whether autonomous loop ``name`` should tick.

    See module docstring for the full resolution table.

    Parameters
    ----------
    name : str
        Loop identifier in lowercase or uppercase. Normalized to the
        ``UA_<NAME>_ENABLED`` / ``UA_DEV_<NAME>_FORCE_ON`` env-var
        conventions.
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
    main_var = f"UA_{suffix}_ENABLED"
    dev_force_var = f"UA_DEV_{suffix}_FORCE_ON"

    if is_development_runtime():
        # Dev mode: defensive against Infisical pollution.
        if _env_truthy(dev_force_var):
            return True
        if _env_falsy(main_var):
            return False
        # Any UA_<NAME>_ENABLED=truthy is IGNORED here — we treat it as
        # historical-prod-parity pollution from Infisical's dev env.
        return False

    # Production / staging / unset: original semantics.
    raw = os.getenv(main_var)
    if raw is not None:
        normalized = raw.strip().lower()
        if normalized in _TRUTHY:
            return True
        if normalized in _FALSY:
            return False
        logger.warning(
            "Unrecognized value for %s=%r — treating as default", main_var, raw
        )
    return prod_default


def explain_loop_decision(name: str, *, prod_default: bool = True) -> str:
    """Human-readable explanation of why ``should_run_loop`` returned what it did.

    Useful in startup logs so operators can grep ``loop_control:`` and see
    every gate decision at boot.
    """
    suffix = _normalize_flag_name(name)
    main_var = f"UA_{suffix}_ENABLED"
    dev_force_var = f"UA_DEV_{suffix}_FORCE_ON"

    if is_development_runtime():
        if _env_truthy(dev_force_var):
            return f"{dev_force_var}=1 (dev opt-in) → ON"
        if _env_falsy(main_var):
            raw = os.getenv(main_var)
            return f"{main_var}={raw} (explicit OFF) → OFF"
        if _env_truthy(main_var):
            raw = os.getenv(main_var)
            return (
                f"{main_var}={raw} present but IGNORED in dev (likely "
                f"Infisical-injected prod-parity); set {dev_force_var}=1 to opt in → OFF"
            )
        return "dev default (UA_RUNTIME_STAGE=development) → OFF"

    raw = os.getenv(main_var)
    if raw is not None:
        normalized = raw.strip().lower()
        if normalized in _TRUTHY or normalized in _FALSY:
            decision = "ON" if normalized in _TRUTHY else "OFF"
            return f"{main_var}={raw} (explicit) → {decision}"
    decision = "ON" if prod_default else "OFF"
    return f"{main_var} unset, prod_default={prod_default} → {decision}"


# Known loops that are gated by should_run_loop, for the dev-startup banner.
# When in dev mode, report_dev_overrides() reads each of these and reports
# whether anything is forcing the loop on/off so the operator can see at a
# glance what's happening.
_KNOWN_LOOPS: Final[tuple[str, ...]] = (
    "heartbeat",
    "heartbeat_autonomous",
    "cron",
    "cron_registration",
    "idle_poll",
    "dispatch_stale_sweep",
    "daemon_sessions",
    "vp_event_bridge",
    "vp_stale_reconcile",
    "agentmail_service",
    "notification_dispatcher",
    "hq_self_heartbeat",
)


def report_dev_overrides(*, log: logging.Logger | None = None) -> None:
    """Emit a startup log of dev-mode loop-gate decisions.

    Called once at gateway/api startup. In production this returns
    immediately. In development it prints a one-line summary per known
    loop showing what should_run_loop decided and why — operators see
    at a glance whether Infisical is forcing anything on/off.

    Parameters
    ----------
    log : logging.Logger, optional
        Logger to use. Defaults to this module's logger.
    """
    target = log or logger
    if not is_development_runtime():
        return
    target.info(
        "🔧 loop_control: UA_RUNTIME_STAGE=development; reporting per-loop decisions..."
    )
    forced_on: list[str] = []
    for name in _KNOWN_LOOPS:
        suffix = _normalize_flag_name(name)
        dev_force_var = f"UA_DEV_{suffix}_FORCE_ON"
        main_var = f"UA_{suffix}_ENABLED"
        msg = explain_loop_decision(name, prod_default=True)
        target.info("   loop_control[%s]: %s", name, msg)
        if _env_truthy(dev_force_var):
            forced_on.append(name)
        if _env_truthy(main_var):
            # Infisical pollution detected but harmless under new semantics.
            target.warning(
                "   loop_control: %s=truthy detected but IGNORED in dev. "
                "Likely Infisical prod-parity injection. Remove from "
                "Infisical development env, or override via UA_DEV_%s_FORCE_ON=1 "
                "if you actually want this loop on.",
                main_var,
                suffix,
            )
    if forced_on:
        target.info(
            "🔧 loop_control: operator opted-in (dev) loops: %s",
            ", ".join(forced_on),
        )
    else:
        target.info("🔧 loop_control: no dev opt-ins; all loops dev-default-OFF.")
