"""Link payments — startup health probe.

Run on UA startup when `UA_ENABLE_LINK=1`. Performs lightweight, side-effect-free
checks to confirm the Link CLI is reachable and authenticated:

  1. Auth blob restoration succeeded (or wasn't needed in stub mode).
  2. `link-cli auth status` returns authenticated=True.
  3. `link-cli payment-methods list` returns at least one payment method.

The probe never creates spend requests, never charges anything, and never
exposes card details. Result is logged + cached on the module for ops tooling
(Phase 2b will surface it via /_ops/link/health).

Failure modes (any → record in `last_probe`, log warning, do NOT raise):

  - cli_not_found      — link-cli binary unreachable
  - auth_unauthenticated — auth blob missing/expired/revoked
  - no_payment_methods — wallet is empty
  - cli_error          — any other CLI failure

The probe is safe to call repeatedly. Phase 2c's reconciler will re-poll
periodically; Phase 2b's ops endpoint will trigger ad-hoc.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from universal_agent import feature_flags
from universal_agent.tools import link_bridge

logger = logging.getLogger(__name__)


_LAST_PROBE: dict[str, Any] = {
    "ran": False,
    "ts": None,
    "ok": False,
    "mode": "unknown",
    "auth_seed": None,
    "auth_status": None,
    "payment_methods_count": 0,
    "error": None,
}


def last_probe() -> dict[str, Any]:
    """Return the most recent probe snapshot. Always safe to call."""
    return dict(_LAST_PROBE)


def run_link_health_probe() -> dict[str, Any]:
    """Run the probe. Idempotent — caller is responsible for scheduling.

    Returns the same dict that gets cached in `last_probe()`.
    """
    snapshot: dict[str, Any] = {
        "ran": True,
        "ts": time.time(),
        "ok": False,
        "mode": "unknown",
        "auth_seed": None,
        "auth_status": None,
        "payment_methods_count": 0,
        "error": None,
    }

    if not feature_flags.link_enabled():
        snapshot.update(
            {
                "mode": "stub",
                "ok": True,
                "auth_seed": {"applied": False, "reason": "link_disabled"},
                "error": None,
            }
        )
        _LAST_PROBE.update(snapshot)
        logger.info("Link health probe skipped: UA_ENABLE_LINK=0 (stub mode).")
        return last_probe()

    seed = link_bridge._ensure_auth_seeded()
    snapshot["auth_seed"] = seed

    auth_result = link_bridge.auth_status(caller="ops")
    snapshot["mode"] = auth_result.get("mode", "unknown")

    if not auth_result["ok"]:
        snapshot["error"] = auth_result.get("error") or {"code": "auth_check_failed"}
        _LAST_PROBE.update(snapshot)
        logger.warning("Link health probe: auth_status failed: %s", snapshot["error"])
        return last_probe()

    auth_data = auth_result["data"] or {}
    snapshot["auth_status"] = {
        "authenticated": bool(auth_data.get("authenticated")),
        "update_available": bool(auth_data.get("update")),
    }

    if not auth_data.get("authenticated"):
        snapshot["error"] = {
            "code": "auth_unauthenticated",
            "message": "Link CLI reports unauthenticated. Run scripts/bootstrap_link_auth.sh and re-seed LINK_AUTH_BLOB in Infisical.",
        }
        _LAST_PROBE.update(snapshot)
        logger.warning("Link health probe: not authenticated.")
        return last_probe()

    pm_result = link_bridge.list_payment_methods(caller="ops")
    if not pm_result["ok"]:
        snapshot["error"] = pm_result.get("error") or {"code": "payment_methods_failed"}
        _LAST_PROBE.update(snapshot)
        logger.warning(
            "Link health probe: payment-methods list failed: %s", snapshot["error"]
        )
        return last_probe()

    pms = (pm_result["data"] or {}).get("payment_methods") or []
    snapshot["payment_methods_count"] = len(pms)

    if not pms:
        snapshot["error"] = {
            "code": "no_payment_methods",
            "message": "No payment methods on file. Add one at https://app.link.com/wallet then redeploy.",
        }
        _LAST_PROBE.update(snapshot)
        logger.warning("Link health probe: wallet has no payment methods.")
        return last_probe()

    snapshot["ok"] = True
    _LAST_PROBE.update(snapshot)
    logger.info(
        "Link health probe: OK (mode=%s, payment_methods=%d, auth=%s)",
        snapshot["mode"],
        snapshot["payment_methods_count"],
        snapshot["auth_status"],
    )
    return last_probe()
