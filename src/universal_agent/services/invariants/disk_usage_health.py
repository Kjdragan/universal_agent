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

PRIMARY DISK DRIVERS (corrected 2026-06-25 after the disk-critical
incident where / hit 92% under sustained VP-coder load):

1. **VP-coder mission ``.venv`` bloat** under
   ``AGENT_RUN_WORKSPACES/vp_coder_primary_external``. Observed 30G
   across 221 mission dirs, 19.6G of it regenerable per-mission ``.venv``
   (+ ``__pycache__`` 0.8G, ``node_modules`` 0.4G). This IS a primary
   driver under sustained VP load — see
   ``scripts/vp_coder_regenerable_reaper.py`` (daily) +
   ``scripts/vp_coder_workspace_pruner.py`` (weekly) for the durable fix.

2. uv cache (``~/.cache/uv``, ``/root/.cache/uv``, ``/tmp/uv_cache``,
   ``<repo>/.uv-cache``). Was the dominant driver on 2026-06-04 but is
   now pruned every deploy by ``scripts/deploy/remote_deploy.sh`` and is
   regularly <1G — so the older "uv cache is THE driver" framing is
   stale. ``uv cache prune`` often finds nothing unused.

Secondary one-time reclaimable: stale containerd build cache
(``docker builder prune``). This probe surfaces real top consumers via
runbook_command. See ``scripts/deploy/remote_deploy.sh`` and
``project_docs/06_platform/04_deployment_and_cicd.md``.

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
import time
from typing import Any, Dict, List, Optional

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


def _consumer_roots() -> list[str]:
    """The heavy directory roots whose immediate subdirs the live scan measures.

    Overridable via UA_DISK_HEALTH_ROOTS (comma-separated). Defaults to the
    known-heavy trees on the production VPS. Missing paths degrade gracefully.
    Naming these live at evaluation time is what keeps the finding honest — the
    design_note's own lesson is "name CATEGORIES, not point-in-time GB, because
    those go stale"; this probe supplies the real GB at the moment it fires.
    """
    raw = (os.getenv("UA_DISK_HEALTH_ROOTS") or "").strip()
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return [
        os.getenv(
            "UA_REMOTE_WORKSPACES_DIR",
            "/opt/universal_agent/AGENT_RUN_WORKSPACES",
        ),
        "/opt/universal_agent/AGENT_RUN_WORKSPACES_ARCHIVE",
        "/opt/ua_demos",
        "/home/ua/ua_scratch_archive",
    ]


# Bounds for the live scan — keep it cheap so a pressured heartbeat never blocks.
_SCAN_DEADLINE_S = float(os.getenv("UA_DISK_HEALTH_SCAN_DEADLINE_S", "2.0"))
_SCAN_MAX_ENTRIES = int(os.getenv("UA_DISK_HEALTH_SCAN_MAX_ENTRIES", "50000"))
_SCAN_TOP_N = int(os.getenv("UA_DISK_HEALTH_SCAN_TOP_N", "5"))


def _dir_size_bounded(path: str, budget: Dict[str, Any]) -> int:
    """Best-effort recursive byte size of ``path``, sharing a global budget.

    Stops early (returning the partial sum measured so far) when the shared
    wall-clock deadline passes or the entry-visit cap is hit, so one huge tree
    can't blow the scan's time bound. Symlinks are not followed; unreadable
    entries are skipped. Never raises.
    """
    total = 0
    stack = [path]
    while stack:
        if time.monotonic() > budget["deadline"] or budget["entries"] >= _SCAN_MAX_ENTRIES:
            break
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    budget["entries"] += 1
                    if budget["entries"] >= _SCAN_MAX_ENTRIES or time.monotonic() > budget["deadline"]:
                        break
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except (OSError, ValueError):
                        continue
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            continue
    return total


def _top_consumers(roots: Optional[list[str]] = None) -> List[Dict[str, Any]]:
    """Live, time-bounded scan naming the largest immediate subdirs of ``roots``.

    Returns up to ``_SCAN_TOP_N`` ``{"path", "size_bytes", "size_gb"}`` entries
    sorted largest first (by raw bytes; ``size_gb`` is a rounded display value),
    aggregated across all roots. Runs only on the (rare) pressured path.
    Missing roots are skipped; the whole scan shares one wall-clock deadline and
    entry-visit budget and returns whatever it measured if it runs out — it never
    raises.
    """
    roots = roots if roots is not None else _consumer_roots()
    budget: Dict[str, Any] = {
        "deadline": time.monotonic() + _SCAN_DEADLINE_S,
        "entries": 0,
    }
    consumers: List[Dict[str, Any]] = []
    for root in roots:
        if time.monotonic() > budget["deadline"] or budget["entries"] >= _SCAN_MAX_ENTRIES:
            break
        try:
            children = list(os.scandir(root))
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            continue
        for entry in children:
            if time.monotonic() > budget["deadline"] or budget["entries"] >= _SCAN_MAX_ENTRIES:
                break
            budget["entries"] += 1
            try:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
            except (OSError, ValueError):
                continue
            size = _dir_size_bounded(entry.path, budget)
            # Keep raw bytes for ordering; size_gb (rounded to 2 decimals) is for
            # display only — sorting on it makes sub-10MB dirs tie and fall back to
            # nondeterministic filesystem order.
            consumers.append({"path": entry.path, "size_bytes": size, "size_gb": _gb(size)})
    consumers.sort(key=lambda c: c["size_bytes"], reverse=True)
    return consumers[:_SCAN_TOP_N]


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
    # Plain-English static fallback (the probe returns a dynamic one with live
    # numbers; this is belt-and-suspenders). No paths/commands — that's what the
    # technical recommendation/runbook below are for.
    operator_summary=(
        "The production server is running low on disk space. Nothing is broken "
        "yet, but if a disk fills completely the agent services start failing. "
        "The fix is to reclaim space — mostly build caches, old per-mission "
        "workspaces, and dangling Docker layers."
    ),
    runbook_command=(
        "df -h; "
        "echo '--- Docker/containerd (often the biggest consumer; only the "
        "dangling/build-cache part is reclaimable, ~few GB) ---'; "
        "sudo docker system df 2>/dev/null; "
        "sudo du -sh /var/lib/docker /var/lib/containerd 2>/dev/null; "
        "echo '--- AGENT_RUN_WORKSPACES + ARCHIVE (regenerable; daily pruner) ---'; "
        "sudo du -sh /opt/universal_agent/AGENT_RUN_WORKSPACES "
        "/opt/universal_agent/AGENT_RUN_WORKSPACES_ARCHIVE 2>/dev/null; "
        "sudo du -sh /opt/universal_agent/AGENT_RUN_WORKSPACES/*/ 2>/dev/null | sort -rh | head -10; "
        "echo '--- /home/ua (stale dev worktrees — operator judgment) ---'; "
        "sudo du -sh /home/ua/* 2>/dev/null | sort -rh | head -10; "
        "echo '--- largest DB files ---'; "
        "sudo ls -laSh /opt/universal_agent/*.db /opt/universal_agent/AGENT_RUN_WORKSPACES/*.db "
        "/var/lib/universal-agent/csi/*.db 2>/dev/null | head -10; "
        "echo '--- uv caches (tiny now; auto-pruned each deploy) ---'; "
        "sudo du -sh /home/ua/.cache/uv /root/.cache/uv /tmp/uv_cache "
        "/opt/universal_agent/.uv-cache 2>/dev/null; "
        "echo '--- SAFE one-time reclaim (keeps running services) ---'; "
        "echo '# regenerable .venv/__pycache__/node_modules reap (the big lever; "
        "runs daily, but force now):'; "
        "echo 'cd /opt/universal_agent && sudo -u ua env "
        "PYTHONPATH=/opt/universal_agent/src .venv/bin/python -m "
        "universal_agent.scripts.vp_coder_regenerable_reaper'; "
        "echo '# whole-dir archival of completed missions (weekly tier):'; "
        "echo 'cd /opt/universal_agent && sudo -u ua env "
        "PYTHONPATH=/opt/universal_agent/src .venv/bin/python -m "
        "universal_agent.scripts.vp_coder_workspace_pruner'; "
        "echo '# docker dangling (note: on this containerd-snapshotter host "
        "system/buildx prune often reclaim ~0B — most images are LIVE):'; "
        "echo 'sudo docker system prune -f'"
    ),
    metadata={
        "design_note": (
            "P5 (2026-05-20): one probe covering multiple mounts in one "
            "finding. Pure shutil.disk_usage call — no DB, no HTTP, no AI "
            "inference, no subprocess (the du diagnostics live in "
            "runbook_command, off the per-heartbeat path — keeps the probe "
            "~1ms and avoids blocking the heartbeat loop). Severity_override "
            "lifts to critical above 90% on any mount. "
            "2026-06-04: previously attributed the growth driver to the uv "
            "cache (~65G) and dismissed AGENT_RUN_WORKSPACES as ~0.3G "
            "reapable. 2026-06-25 CORRECTION: that framing was stale and "
            "actively misled cycles into dismissing real VP-coder bloat. "
            "Under sustained VP load the per-mission .venv bloat under "
            "AGENT_RUN_WORKSPACES/vp_coder_primary_external IS a primary "
            "driver (30G observed, 19.6G regenerable .venv), and the uv "
            "cache is regularly <1G (remote_deploy.sh prunes it every "
            "deploy). The durable fix is the daily regenerable-artifact "
            "reaper (scripts/vp_coder_regenerable_reaper.py) plus the "
            "weekly whole-dir pruner (scripts/vp_coder_workspace_pruner.py). "
            "Lesson going forward: name reclaim CATEGORIES + commands, not "
            "absolute point-in-time GB (those go stale); the runbook surfaces "
            "live `du -sh`. (Docker/containerd also occupies ~59G on the VPS "
            "but is mostly LIVE running containers — only a few GB dangling is "
            "actually reclaimable.) "
            "2026-07-07: the finding now ALSO carries a live, time-bounded "
            "top-subdir scan (_top_consumers, run only on the pressured path) "
            "so the message names the REAL current consumers with fresh GB at "
            "evaluation time — no more static point-in-time driver claim. The "
            "scan shares a wall-clock deadline + entry-visit cap and degrades "
            "to a partial/empty result rather than blocking the heartbeat; "
            "roots are overridable via UA_DISK_HEALTH_ROOTS."
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
    worst_free_gb = min((m["free_gb"] for m in pressured), default=0.0)

    # Live, bounded scan of the heavy roots — runs ONLY here, on the (rare)
    # pressured path, so the healthy heartbeat stays a pure syscall. This names
    # the REAL current top consumers at evaluation time instead of a static
    # point-in-time claim (the design_note's own lesson). Never raises.
    try:
        top_consumers = _top_consumers()
    except Exception:  # noqa: BLE001 — fail-open: a scan hiccup must not drop the finding
        logger.debug("disk_usage_health: top-consumer scan failed", exc_info=True)
        top_consumers = []

    if top_consumers:
        live_consumer_text = (
            "Live scan of the heavy roots right now: "
            + ", ".join(f"{c['path']} ({c['size_gb']}G)" for c in top_consumers)
            + ". "
        )
    else:
        live_consumer_text = (
            "Live top-consumer scan found nothing under the known roots (paths "
            "absent on this host or the scan budget was hit) — run the runbook "
            "for `du -sh`. "
        )

    return {
        "observed_value": {
            "pressured_mounts": pressured,
            "healthy_mounts": healthy,
            "worst_used_pct": worst_pct,
            "top_consumers": top_consumers,
        },
        "threshold_text": (
            f"every monitored mount used_pct ≤ {WARN_THRESHOLD_PCT}% "
            f"(critical above {CRITICAL_THRESHOLD_PCT}%)"
        ),
        # Plain-English lead with LIVE numbers (the email leads with this).
        "operator_summary": (
            f"The production server's disk is {worst_pct:.0f}% full "
            f"(about {worst_free_gb:.0f} GB free) and slowly filling up. Nothing "
            "is broken yet, but if it reaches ~100% the agent services start "
            "failing. Most of the space is in active use (Docker images + running "
            "containers); the safely reclaimable part is build caches, old "
            "per-mission workspaces, and dangling Docker layers."
        ),
        # Technical recommendation (for Claude / handoff). Names reclaim
        # CATEGORIES + commands, plus a LIVE largest-first scan measured at
        # evaluation time (live_consumer_text) — not the hardcoded point-in-time
        # GB figures that go stale (see the 2026-06-25 design_note).
        "message": (
            f"Disk pressure on {len(pressured)} mount(s): {', '.join(mount_names)}. "
            f"Worst {worst_pct}%. {live_consumer_text}"
            "Top reclaimable consumers under sustained "
            "VP-coder load are the per-mission .venv / __pycache__ / "
            "node_modules trees under "
            "AGENT_RUN_WORKSPACES/vp_coder_primary_external (the daily "
            "regenerable-artifact reaper scripts/vp_coder_regenerable_reaper.py "
            "+ the weekly whole-dir pruner "
            "scripts/vp_coder_workspace_pruner.py own the durable fix), "
            "followed by the uv cache (~/.cache/uv + /root/.cache/uv + "
            "/tmp/uv_cache + <repo>/.uv-cache — auto-pruned each deploy via "
            "scripts/deploy/remote_deploy.sh, regularly <1G so 'uv cache "
            "prune' often finds nothing unused). Run the runbook for live "
            "`du -sh` of actual top consumers."
        ),
        "severity_override": severity,
    }
