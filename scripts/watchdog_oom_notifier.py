#!/usr/bin/env python3
"""Detect recent OOM events and publish a dashboard notification."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

STATE_FILE = Path(
    os.getenv("UA_OOM_ALERT_STATE_FILE", "/var/lib/universal-agent/watchdog/oom_notifier_state.json")
).expanduser()
LOOKBACK_SECONDS = max(30, int(os.getenv("UA_OOM_ALERT_LOOKBACK_SECONDS", "180") or "180"))
ENDPOINT = (os.getenv("UA_OOM_ALERT_ENDPOINT") or "http://127.0.0.1:8002/api/v1/ops/notifications").strip()
OPS_TOKEN = (os.getenv("UA_OPS_TOKEN") or "").strip()

KERNEL_OOM_PATTERN = re.compile(
    r"(oom-kill:|Out of memory: Killed process|killed by the OOM killer|oom_reaper: reaped process)",
    re.IGNORECASE,
)
SERVICE_OOM_PATTERN = re.compile(
    r"(A process of this unit has been killed by the OOM killer)",
    re.IGNORECASE,
)


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.stdout or ""


def _collect_oom_lines(since_epoch: int) -> list[str]:
    lines: list[str] = []
    kernel_output = _run(
        ["journalctl", "--no-pager", "-k", "--since", f"@{since_epoch}", "-o", "short-iso"]
    )
    for line in kernel_output.splitlines():
        if KERNEL_OOM_PATTERN.search(line):
            lines.append(line.strip())

    service_output = _run(
        [
            "journalctl",
            "--no-pager",
            "--since",
            f"@{since_epoch}",
            "-u",
            "universal-agent-gateway.service",
            "-o",
            "short-iso",
        ]
    )
    for line in service_output.splitlines():
        if SERVICE_OOM_PATTERN.search(line):
            lines.append(line.strip())
    return lines


def _post_notification(lines: list[str]) -> tuple[bool, str]:
    if not ENDPOINT:
        return False, "missing_endpoint"
    if not OPS_TOKEN:
        return False, "missing_ops_token"

    summary = lines[0]
    message = f"OOM kill detected on VPS. {summary}"
    if len(lines) > 1:
        message += f" (+{len(lines) - 1} related lines)"

    payload = {
        "kind": "system_oom_kill",
        "title": "VPS OOM Kill Detected",
        "message": message[:800],
        "severity": "error",
        "requires_action": True,
        "metadata": {
            "source": "oom_notifier",
            "count": len(lines),
            "sample": lines[:6],
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=data,
        headers={
            "content-type": "application/json",
            "x-ua-ops-token": OPS_TOKEN,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            if not (200 <= resp.status < 300):
                return False, f"status_{resp.status}:{body[:200]}"
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        return False, f"http_{exc.code}:{body[:200]}"
    except Exception as exc:
        return False, f"{type(exc).__name__}:{exc}"
    return True, "ok"


def main() -> int:
    now_epoch = int(time.time())
    state = _load_state()
    since_epoch = int(state.get("last_checked_epoch") or max(0, now_epoch - LOOKBACK_SECONDS))
    if since_epoch > now_epoch:
        since_epoch = now_epoch - LOOKBACK_SECONDS

    lines = _collect_oom_lines(since_epoch)
    if not lines:
        _save_state({"last_checked_epoch": now_epoch})
        print("OOM_NOTIFIER_NO_EVENTS")
        return 0

    sent, reason = _post_notification(lines)
    print(f"OOM_NOTIFIER_EVENTS={len(lines)} sent={int(sent)} reason={reason}")
    if sent:
        _save_state(
            {
                "last_checked_epoch": now_epoch,
                "last_sent_epoch": now_epoch,
                "last_sent_count": len(lines),
                "last_sent_reason": reason,
            }
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
