"""Synthetic-notification probe for end-to-end dispatch verification.

POSTs a high-severity notification via the gateway's ops API, waits
for the F3 NotificationDispatcher to drain it to email + Telegram, and
verifies both `metadata.delivery.email.delivered_at` and
`metadata.delivery.telegram.delivered_at` are populated.

Operator workflow on the VPS post-deploy:

    UA_OPS_TOKEN=$(infisical secrets get UA_OPS_TOKEN --plain) \\
    UA_GATEWAY_URL=http://127.0.0.1:8002 \\
    python scripts/probe_notification_dispatch.py --wait 90 --cleanup

Exit codes:
    0   Both channels delivered within the wait window.
    1   POST failed (gateway unreachable, auth bad, etc.).
    2   Probe row not visible in the dashboard read-back.
    3   One or both channels did not deliver within the wait window.

Use `--cleanup` to dismiss the probe row after verification so the
dashboard does not accumulate probe noise.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx


@dataclass
class ProbeResult:
    exit_code: int = 0
    probe_id: str = ""
    notification_id: str = ""
    row_found: bool = False
    delivered: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    post_failed: bool = False
    error: str = ""


def _build_payload(probe_id: str) -> dict[str, Any]:
    return {
        "kind": "ops_probe_alert",
        "title": "Probe — please ignore",
        "message": (
            f"Synthetic notification probe (id={probe_id}). This row was "
            f"posted to verify the email + Telegram dispatch path is "
            f"working end-to-end.  No action required."
        ),
        "severity": "error",
        "requires_action": False,
        "metadata": {
            "probe_id": probe_id,
            "source": "probe_notification_dispatch",
        },
    }


def _find_probe_row(payload: dict[str, Any], probe_id: str) -> Optional[dict[str, Any]]:
    notifications = payload.get("notifications") if isinstance(payload, dict) else None
    if not isinstance(notifications, list):
        return None
    for row in notifications:
        if not isinstance(row, dict):
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if str(metadata.get("probe_id") or "") == probe_id:
            return row
    return None


def _check_delivery(row: dict[str, Any]) -> tuple[list[str], list[str]]:
    delivered: list[str] = []
    missing: list[str] = []
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    delivery = metadata.get("delivery") if isinstance(metadata.get("delivery"), dict) else {}
    for channel in ("email", "telegram"):
        entry = delivery.get(channel)
        if isinstance(entry, dict) and entry.get("delivered_at"):
            delivered.append(channel)
        else:
            missing.append(channel)
    return delivered, missing


def run_probe(
    *,
    base_url: str,
    ops_token: str,
    wait_seconds: int = 90,
    probe_id: Optional[str] = None,
    cleanup: bool = False,
    transport: Optional[httpx.BaseTransport] = None,
) -> ProbeResult:
    pid = probe_id or f"probe-{uuid.uuid4().hex[:10]}"
    result = ProbeResult(probe_id=pid)

    headers = {"Authorization": f"Bearer {ops_token}", "Content-Type": "application/json"}
    client_kwargs: dict[str, Any] = {"base_url": base_url, "headers": headers, "timeout": 15.0}
    if transport is not None:
        client_kwargs["transport"] = transport

    with httpx.Client(**client_kwargs) as client:
        try:
            resp = client.post("/api/v1/ops/notifications", json=_build_payload(pid))
        except Exception as exc:
            result.exit_code = 1
            result.post_failed = True
            result.error = f"POST exception: {exc}"
            return result
        if resp.status_code >= 400:
            result.exit_code = 1
            result.post_failed = True
            result.error = f"POST returned HTTP {resp.status_code}: {resp.text[:200]}"
            return result
        body = resp.json() if resp.content else {}
        notification = body.get("notification") if isinstance(body, dict) else None
        if isinstance(notification, dict):
            result.notification_id = str(notification.get("id") or "")

        if wait_seconds > 0:
            time.sleep(wait_seconds)

        try:
            read_resp = client.get("/api/v1/dashboard/notifications", params={"limit": 200})
        except Exception as exc:
            result.exit_code = 2
            result.error = f"Dashboard read-back exception: {exc}"
            return result
        if read_resp.status_code >= 400:
            result.exit_code = 2
            result.error = f"Dashboard read-back HTTP {read_resp.status_code}: {read_resp.text[:200]}"
            return result

        row = _find_probe_row(read_resp.json(), pid)
        if row is None:
            result.exit_code = 2
            result.error = f"Probe row probe_id={pid} not found in dashboard read-back."
            return result
        result.row_found = True

        delivered, missing = _check_delivery(row)
        result.delivered = delivered
        result.missing = missing
        if missing:
            result.exit_code = 3
            result.error = (
                f"Channels did not deliver within {wait_seconds}s: {missing}. "
                f"Delivered: {delivered}.  Check NotificationDispatcher logs."
            )
            return result

        if cleanup and result.notification_id:
            try:
                client.patch(
                    f"/api/v1/dashboard/notifications/{result.notification_id}",
                    json={"status": "dismissed"},
                )
            except Exception:
                pass

    return result


def _format_summary(result: ProbeResult) -> str:
    lines = [
        f"probe_id        : {result.probe_id}",
        f"notification_id : {result.notification_id or '(unknown)'}",
        f"row_found       : {result.row_found}",
        f"delivered       : {', '.join(result.delivered) if result.delivered else '(none)'}",
        f"missing         : {', '.join(result.missing) if result.missing else '(none)'}",
        f"exit_code       : {result.exit_code}",
    ]
    if result.error:
        lines.append(f"error           : {result.error}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("UA_GATEWAY_URL") or "http://127.0.0.1:8002")
    parser.add_argument("--ops-token", default=os.getenv("UA_OPS_TOKEN") or "")
    parser.add_argument("--wait", type=int, default=90, help="Seconds to wait between POST and read-back.")
    parser.add_argument("--probe-id", default=None)
    parser.add_argument("--cleanup", action="store_true", help="Dismiss the probe row after verification.")
    args = parser.parse_args()

    if not args.ops_token:
        print("ERROR: --ops-token (or UA_OPS_TOKEN env) is required.", file=sys.stderr)
        return 1

    result = run_probe(
        base_url=args.base_url.rstrip("/"),
        ops_token=args.ops_token,
        wait_seconds=int(args.wait),
        probe_id=args.probe_id,
        cleanup=bool(args.cleanup),
    )
    print(_format_summary(result))
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
