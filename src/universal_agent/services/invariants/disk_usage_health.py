"""Disk usage health invariant — P5 of the watchdog restoration.

Simone's 2026-05-20 morning digest flagged disk at 70% with a "climbing"
trend and recommended an invariant for proactive detection before
disk-full becomes an outage. P5 adds one.

Same lightweight pattern as P4 zai_inference_health: pure-syscall probe
(`shutil.disk_usage()`), no DB, no AI inference, no HTTP. Cost per
heartbeat: ~1ms across the monitored mounts.

Monitored mounts (overridable via UA_DISK_HEALTH_MOUNTS env var, comma-
separated; on the production VPS all three resolve to the single /dev/sda1
partition):
- `/` — root filesystem (general disk pressure)
- `/opt` — where AGENT_RUN_WORKSPACES + the repo's .uv-cache live
- `/var/lib` — where csi.db + activity_state.db + runtime_state.db live

The dominant growth driver (confirmed 2026-06-04) is the unpruned uv cache
(~65G across ua ~/.cache/uv, root /root/.cache/uv, /tmp/uv_cache, and the
repo .uv-cache), NOT AGENT_RUN_WORKSPACES or the DBs. remote_deploy.sh now
prunes the uv caches on every deploy; this probe surfaces real top consumers
via runbook_command. See scripts/deploy/remote_deploy.sh and
project_docs/06_platform/04_deployment_and_cicd.md.

Severity (strict per operator pattern set in P4):
- WARN: any mount above 75%
- CRITICAL: any mount above 90%
- Worst-of across mounts drives severity_override
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
from typing import Any, Dict, Optional

from universal_agent.services.pipeline_invariants import invariant

logger = logging.getLogger(__name__)


WARN_THRESHOLD_PCT = float(os.getenv("UA_DISK_HEALTH_WARN_PCT", "75"))
CRITICAL_THRESHOLD_PCT = float(os.getenv("UA_DISK_HEALTH_CRITICAL_PCT", "90"))


def _default_mounts() -> list[str]:
    raw = (os.getenv("UA_DISK_HEALTH_MOUNTS") or "").strip()
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return ["/", "/opt", "/var/lib"]


def _gb(num_bytes: int) -> float:
    return round(num_bytes / (1024 ** 3), 2)


def _measure_mount(path: str) -> Optional[Dict[str, Any]]:
    try:
        usage = shutil.disk_usage(path)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        # Dev box without this mount, or transient — skip silently per
        # watchdog fail-open contract.
        logger.debug("disk_usage_health: skip %s (%s)", path, exc)
        return None
    if usage.total <= 0:
        return None
    used_pct = round((usage.used / usage.total) * 100, 1)
    return {
        "mount": path,
        "total_gb": _gb(usage.total),
        "used_gb": _gb(usage.used),
        "free_gb": _gb(usage.free),
        "used_pct": used_pct,
    }


@invariant(
    id="disk_usage_health",
    title="Disk usage across monitored mounts within safe range",
    description=(
        "Watches the disk-usage percentage on root, /opt (AGENT_RUN_WORKSPACES "
        "lives here), and /var/lib (canonical DB locations). Fires when any "
        "mount is above the warn threshold (75%) and critical above 90%. "
        "Added 2026-05-20 (P5) after Simone's morning digest flagged 70% "
        "and climbing — proactive coverage before disk-full becomes outage."
    ),
    severity="warn",
    runbook_command=(
        "df -h; "
        "echo '--- uv caches (top reclaimable consumer; auto-pruned each deploy) ---'; "
        "sudo du -sh /home/ua/.cache/uv /root/.cache/uv /tmp/uv_cache "
        "/opt/universal_agent/.uv-cache 2>/dev/null; "
        "echo '--- AGENT_RUN_WORKSPACES total + largest subdirs ---'; "
        "sudo du -sh /opt/universal_agent/AGENT_RUN_WORKSPACES 2>/dev/null; "
        "sudo du -sh /opt/universal_agent/AGENT_RUN_WORKSPACES/*/ 2>/dev/null | sort -rh | head -10; "
        "echo '--- largest DB files ---'; "
        "sudo ls -laSh /opt/universal_agent/*.db /opt/universal_agent/AGENT_RUN_WORKSPACES/*.db "
        "/var/lib/universal-agent/csi/*.db 2>/dev/null | head -10; "
        "echo '--- manual one-time uv prune (if no deploy is imminent) ---'; "
        "echo 'sudo -H -u ua uv cache prune --ci --force; "
        "sudo -H -u ua env UV_CACHE_DIR=/tmp/uv_cache uv cache prune --ci --force; "
        "sudo -H -u ua env UV_CACHE_DIR=/opt/universal_agent/.uv-cache uv cache prune --ci --force; "
        "sudo -H env HOME=/root /root/.local/bin/uv cache prune --ci --force'"
    ),
    metadata={
        "design_note": (
            "P5 (2026-05-20): one probe covering multiple mounts in one "
            "finding. Pure shutil.disk_usage call — no DB, no HTTP, no AI "
            "inference, no subprocess (the du diagnostics live in "
            "runbook_command, off the per-heartbeat path — keeps the probe "
            "~1ms and avoids blocking the heartbeat loop). Severity_override "
            "lifts to critical above 90% on any mount. 2026-06-04: corrected "
            "the cleanup recommendation — the real growth driver is the "
            "unpruned uv cache (~65G across ua/root/tmp/repo trees), not "
            ">14-day AGENT_RUN_WORKSPACES dirs (~0.3G reapable). "
            "remote_deploy.sh now prunes the uv caches on every deploy."
        ),
        "thresholds": {
            "warn_pct": WARN_THRESHOLD_PCT,
            "critical_pct": CRITICAL_THRESHOLD_PCT,
        },
    },
)
def disk_usage_health(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Flag monitored mounts whose disk usage exceeds the warn/critical floors.

    Measures usage on the default mounts (root, ``/opt``, ``/var/lib``) via a
    pure ``shutil.disk_usage`` call — no DB, HTTP, or inference. Any mount above
    the warn threshold produces a finding, and a ``severity_override`` lifts it
    to critical above the critical threshold. Returns None when every mount is
    within safe range. ``ctx`` is unused (signature kept for the probe contract).
    """
    mounts = _default_mounts()
    pressured: list[Dict[str, Any]] = []
    healthy: list[Dict[str, Any]] = []
    worst_pct = 0.0

    for path in mounts:
        m = _measure_mount(path)
        if m is None:
            continue
        if m["used_pct"] > WARN_THRESHOLD_PCT:
            pressured.append(m)
            worst_pct = max(worst_pct, m["used_pct"])
        else:
            healthy.append(m)

    if not pressured:
        return None

    severity = "critical" if worst_pct > CRITICAL_THRESHOLD_PCT else "warn"

    mount_names = sorted(m["mount"] for m in pressured)
    return {
        "observed_value": {
            "pressured_mounts": pressured,
            "healthy_mounts": healthy,
            "worst_used_pct": worst_pct,
        },
        "threshold_text": (
            f"every monitored mount used_pct ≤ {WARN_THRESHOLD_PCT}% "
            f"(critical above {CRITICAL_THRESHOLD_PCT}%)"
        ),
        "message": (
            f"Disk pressure on {len(pressured)} mount(s): {', '.join(mount_names)}. "
            f"Worst {worst_pct}%. Top reclaimable consumer is the uv cache "
            "(~/.cache/uv + /root/.cache/uv + /tmp/uv_cache + <repo>/.uv-cache), "
            "now auto-pruned each deploy via scripts/deploy/remote_deploy.sh "
            "(uv cache prune --ci --force). Run the runbook for live `du -sh` of "
            "actual top consumers — AGENT_RUN_WORKSPACES holds only ~0.3G of "
            "reapable >14-day dirs and is NOT the driver."
        ),
        "severity_override": severity,
    }
