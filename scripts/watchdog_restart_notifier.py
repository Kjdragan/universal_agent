#!/usr/bin/env python3
"""Publish a dashboard/email/Telegram notification when the service watchdog
restarts (or backs off from restarting) a unit.

Why this exists
---------------
``scripts/vps_service_watchdog.sh`` runs as a oneshot systemd unit that sources
``/opt/universal_agent/.env``. ``UA_OPS_TOKEN`` is **not** in ``.env`` — it is an
Infisical-managed secret injected at runtime — so a bare ``curl`` POST from the
bash watchdog would hit ``/api/v1/ops/notifications`` with no auth and get a 401.

This helper bridges that gap the same way the secret-bearing systemd timers do:
it calls ``initialize_runtime_secrets()`` to load secrets from Infisical, then
reads ``UA_OPS_TOKEN`` and POSTs the notification. The POST rides the existing
``NotificationDispatcher`` fan-out (email + Telegram, with cooldown/rollup), so
a flapping/restarting service is no longer silent.

It is invoked best-effort by the watchdog (``|| true``); any failure here must
never block or fail a restart.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

# Make the universal_agent package importable when this top-level script is run
# directly (the watchdog invokes it as `python3 scripts/watchdog_restart_notifier.py`
# from WorkingDirectory=/opt/universal_agent, where the package lives under src/).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _bootstrap_secrets() -> str:
    """Best-effort load of runtime secrets (incl. UA_OPS_TOKEN) from Infisical.

    Returns a short status string for logging; never raises.
    """
    if (os.getenv("UA_OPS_TOKEN") or "").strip():
        return "token_already_present"
    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets

        # profile defaults to vps resolution on the box; pass explicitly so the
        # strict Infisical backstop engages even if UA_RUNTIME_PROFILE is unset.
        initialize_runtime_secrets(profile=os.getenv("UA_WATCHDOG_SECRET_PROFILE") or "vps")
        return "bootstrapped"
    except Exception as exc:  # noqa: BLE001 - best-effort, must not raise
        return f"bootstrap_failed:{type(exc).__name__}"


def _build_payload(args: argparse.Namespace) -> dict:
    escalated = bool(args.escalated)
    if args.event == "flapping_backoff":
        title = f"Watchdog backing off: {args.service} is flapping"
        message = (
            f"{args.service} has been restarted {args.restart_count}x in the last "
            f"{args.window_seconds // 60}m (>= limit {args.max_per_hour}/h). Watchdog is "
            f"backing off auto-restart and escalating. Last trigger: {args.reason}."
        )
        severity = "error"
        requires_action = True
    else:
        verb = "restarted (flapping)" if escalated else "restarted"
        title = f"Watchdog {verb}: {args.service}"
        message = (
            f"Service watchdog {verb} {args.service} (reason: {args.reason}; "
            f"post-state: {args.post_state}). {args.restart_count}x in the last "
            f"{args.window_seconds // 60}m."
        )
        severity = "error" if escalated else "warning"
        requires_action = escalated

    return {
        "kind": "service_watchdog_restart",
        "title": title[:200],
        "message": message[:800],
        "severity": severity,
        "requires_action": requires_action,
        "metadata": {
            "source": "service_watchdog",
            "service": args.service,
            "event": args.event,
            "reason": args.reason,
            "post_state": args.post_state,
            "restart_count_window": args.restart_count,
            "window_seconds": args.window_seconds,
            "max_per_hour": args.max_per_hour,
            "escalated": escalated,
        },
    }


def _post(endpoint: str, ops_token: str, payload: dict) -> tuple[bool, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "content-type": "application/json",
            "x-ua-ops-token": ops_token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            if not (200 <= resp.status < 300):
                return False, f"status_{resp.status}:{body[:160]}"
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        return False, f"http_{exc.code}:{body[:160]}"
    except Exception as exc:  # noqa: BLE001 - best-effort
        return False, f"{type(exc).__name__}:{exc}"
    return True, "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", required=True)
    parser.add_argument("--reason", default="")
    parser.add_argument(
        "--event",
        default="restart",
        choices=["restart", "flapping_backoff"],
        help="restart = a restart happened; flapping_backoff = restart skipped due to flap rate-limit",
    )
    parser.add_argument("--post-state", default="unknown")
    parser.add_argument("--restart-count", type=int, default=0)
    parser.add_argument("--window-seconds", type=int, default=3600)
    parser.add_argument("--max-per-hour", type=int, default=6)
    parser.add_argument("--escalated", action="store_true")
    parser.add_argument(
        "--endpoint",
        default=(os.getenv("UA_WATCHDOG_NOTIFY_ENDPOINT") or "http://127.0.0.1:8002/api/v1/ops/notifications").strip(),
    )
    args = parser.parse_args()

    boot_status = _bootstrap_secrets()
    ops_token = (os.getenv("UA_OPS_TOKEN") or "").strip()
    if not ops_token:
        print(f"WATCHDOG_NOTIFY service={args.service} sent=0 reason=missing_ops_token boot={boot_status}")
        return 1

    payload = _build_payload(args)
    sent, reason = _post(args.endpoint, ops_token, payload)
    print(
        f"WATCHDOG_NOTIFY service={args.service} event={args.event} sent={int(sent)} "
        f"reason={reason} boot={boot_status}"
    )
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(main())
