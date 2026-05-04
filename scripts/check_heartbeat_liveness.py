"""Heartbeat liveness diagnostic.

Read-only health probe.  Hits `/api/v1/dashboard/todolist/overview`,
extracts the heartbeat block, and reports whether the heartbeat is
ticking within an acceptable staleness window (default: 2x the
configured interval).

Usage:

    python scripts/check_heartbeat_liveness.py \\
        --base-url http://127.0.0.1:8002

Exit codes:
    0   Heartbeat is fresh (or explicitly disabled).
    1   Dashboard API returned an error.
    2   Heartbeat has never ticked (never-ticked shape — the
        2026-05-01 silence symptom).
    3   Heartbeat is stale (last tick more than 2x interval ago).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx


@dataclass
class HeartbeatCheckResult:
    exit_code: int = 0
    enabled: bool = True
    disabled: bool = False
    fresh: bool = False
    never_ticked: bool = False
    api_failed: bool = False
    last_run_epoch: Optional[int] = None
    interval_seconds: int = 0
    staleness_seconds: int = 0
    error: str = ""


_DEFAULT_STALENESS_MULTIPLIER = 2.0


def run_check(
    *,
    base_url: str,
    transport: Optional[httpx.BaseTransport] = None,
    now_fn: Callable[[], float] = time.time,
    staleness_multiplier: float = _DEFAULT_STALENESS_MULTIPLIER,
) -> HeartbeatCheckResult:
    result = HeartbeatCheckResult()
    client_kwargs: dict[str, Any] = {"base_url": base_url, "timeout": 10.0}
    if transport is not None:
        client_kwargs["transport"] = transport

    with httpx.Client(**client_kwargs) as client:
        try:
            resp = client.get("/api/v1/dashboard/todolist/overview")
        except Exception as exc:
            result.exit_code = 1
            result.api_failed = True
            result.error = f"GET overview exception: {exc}"
            return result
        if resp.status_code >= 400:
            result.exit_code = 1
            result.api_failed = True
            result.error = f"GET overview returned HTTP {resp.status_code}: {resp.text[:200]}"
            return result
        body = resp.json() if resp.content else {}

    heartbeat = body.get("heartbeat") if isinstance(body, dict) else None
    if not isinstance(heartbeat, dict):
        result.exit_code = 1
        result.api_failed = True
        result.error = "Dashboard response is missing the `heartbeat` block."
        return result

    result.enabled = bool(heartbeat.get("enabled"))
    if not result.enabled:
        result.disabled = True
        result.exit_code = 0  # Explicitly disabled is not a failure.
        return result

    interval = int(heartbeat.get("heartbeat_effective_interval_seconds") or 0)
    result.interval_seconds = interval

    last_epoch = heartbeat.get("latest_last_run_epoch")
    if last_epoch is None:
        result.never_ticked = True
        result.exit_code = 2
        result.error = (
            "Heartbeat has never produced a tick "
            "(latest_last_run_epoch=None).  This is the 2026-05-01 silence "
            "shape — check service startup notifications "
            "(`kind=service_startup_failed`) for the cause."
        )
        return result

    last_epoch = int(last_epoch)
    result.last_run_epoch = last_epoch
    now_ts = int(now_fn())
    staleness = max(0, now_ts - last_epoch)
    result.staleness_seconds = staleness

    threshold = max(60, int(interval * staleness_multiplier)) if interval else 600
    if staleness <= threshold:
        result.fresh = True
        result.exit_code = 0
    else:
        result.fresh = False
        result.exit_code = 3
        result.error = (
            f"Heartbeat stale: last tick {staleness}s ago (threshold "
            f"{threshold}s = {staleness_multiplier}x interval {interval}s)."
        )
    return result


def _format_summary(result: HeartbeatCheckResult) -> str:
    lines = [
        f"enabled        : {result.enabled}",
        f"disabled       : {result.disabled}",
        f"fresh          : {result.fresh}",
        f"never_ticked   : {result.never_ticked}",
        f"api_failed     : {result.api_failed}",
        f"last_run_epoch : {result.last_run_epoch}",
        f"interval_secs  : {result.interval_seconds}",
        f"staleness_secs : {result.staleness_seconds}",
        f"exit_code      : {result.exit_code}",
    ]
    if result.error:
        lines.append(f"error          : {result.error}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("UA_GATEWAY_URL") or "http://127.0.0.1:8002")
    parser.add_argument(
        "--staleness-multiplier",
        type=float,
        default=_DEFAULT_STALENESS_MULTIPLIER,
        help="Heartbeat is considered stale when last tick is older than "
             "(multiplier x interval).  Default: 2.0",
    )
    args = parser.parse_args()

    result = run_check(
        base_url=args.base_url.rstrip("/"),
        staleness_multiplier=float(args.staleness_multiplier),
    )
    print(_format_summary(result))
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
