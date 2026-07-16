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

WHERE THE SPACE IS: **measured, never guessed.** This module deliberately
holds no narrative about which directory is "the driver" — every such claim
written here has gone stale and then actively misled an operator. The
2026-07-15 incident: the probe reported a 0.04G dir as the top consumer
while 7.2G sat in the very root it was scanning and ~60G sat in roots it
never looked at (``/home/ua/.cache``, ``/opt/universal_agent/.worktrees``,
``/home/ua/.claude-science``, ``/var/lib/containerd``). Two causes, both
fixed here:

1. ``_consumer_roots`` was a hand-maintained allowlist of four leaf dirs,
   structurally blind to most of the disk. It now names the *parents*
   everything lives under, so new consumers (including dot-dirs) are
   discovered rather than enumerated.
2. The scan was a Python byte-walk under a shared 2s / 50k-entry budget.
   It blew the budget, then reported the partial sums **as if they were
   real sizes**. It now shells to ``du -x --max-depth=1`` (accurate, and
   ~30s for the whole box) behind a TTL cache refreshed on a background
   thread — so the probe itself never walks the filesystem.

The probe stays a pure ``shutil.disk_usage`` syscall on the hot path. This
matters: ``gateway_server.py::ops_proactive_health`` is ``async def`` and
awaits this probe directly on the event loop, so any in-probe filesystem
walk blocks the gateway (see the 2026-05-26 event-loop starvation incident).
Reclaim *categories* + live measurements live in the finding itself; see
``project_docs/06_platform/04_deployment_and_cicd.md``.

Severity (strict per operator pattern set in P4):
- WARN: any mount above 75%
- CRITICAL: any mount above 90%
- Worst-of across mounts drives severity_override
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
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
    """The roots whose immediate subdirs the scan measures.

    Overridable via UA_DISK_HEALTH_ROOTS (comma-separated). Missing paths
    degrade gracefully.

    These are deliberately the *parent* dirs everything lives under, not a
    curated list of known-heavy leaves. A leaf allowlist only finds what
    someone already thought to add: the pre-2026-07-15 list named four leaves
    totalling ~17G of a 170G-used disk and never saw ``/home/ua/.cache`` (18G)
    or ``.worktrees`` (16G). Scanning parents means a consumer that did not
    exist when this list was written still gets found.
    """
    raw = (os.getenv("UA_DISK_HEALTH_ROOTS") or "").strip()
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return [
        "/opt/universal_agent",
        "/opt",
        "/home/ua",
        "/var/lib",
        "/tmp",
        "/root",
    ]


# Scan bounds. The scan runs on a background thread (never the probe/event
# loop), so it is bounded by a generous wall clock rather than a tiny budget
# that silently truncates. A full-box `du` measures ~30s on the production VPS.
_SCAN_TIMEOUT_S = float(os.getenv("UA_DISK_HEALTH_SCAN_TIMEOUT_S", "180"))
_SCAN_TOP_N = int(os.getenv("UA_DISK_HEALTH_SCAN_TOP_N", "8"))
# How long a measurement stays servable before a refresh is kicked off.
_SCAN_TTL_S = float(os.getenv("UA_DISK_HEALTH_SCAN_TTL_S", "21600"))  # 6h

_scan_lock = threading.Lock()
_scan_state: Dict[str, Any] = {"as_of": 0.0, "consumers": [], "refreshing": False}


def _du_children(root: str) -> List[Dict[str, Any]]:
    """Measure the immediate subdirs of ``root`` with ``du``. Never raises.

    Shells to `du -x --max-depth=1 -B1` rather than walking in Python: it is
    accurate (the Python walk it replaces silently reported truncated partial
    sums as real sizes), it stays on one filesystem (-x), and it does not
    follow symlinks. Returns [] for a missing/unreadable root.
    """
    try:
        proc = subprocess.run(
            ["du", "-x", "--max-depth=1", "-B1", root],
            capture_output=True,
            text=True,
            timeout=_SCAN_TIMEOUT_S,
            # Minimal env: `du` is an installed system tool, not first-party
            # code — do not hand it the process environment (Infisical secrets
            # live there). See the least-privilege rule in CLAUDE.md.
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("disk_usage_health: du failed for %s", root, exc_info=True)
        return []

    root_real = os.path.realpath(root)
    out: List[Dict[str, Any]] = []
    # `du` exits non-zero on partial permission errors but still prints what it
    # could read, so parse stdout regardless of returncode.
    for line in (proc.stdout or "").splitlines():
        raw_size, _, path = line.partition("\t")
        path = path.strip()
        if not path:
            continue
        try:
            size = int(raw_size)
        except ValueError:
            continue
        # --max-depth=1 also prints the root's own total; keep children only.
        if os.path.realpath(path) == root_real:
            continue
        out.append({"path": path, "size_bytes": size, "size_gb": _gb(size)})
    return out


def _top_consumers(roots: Optional[list[str]] = None) -> List[Dict[str, Any]]:
    """Measure the largest immediate subdirs across ``roots``, largest first.

    Synchronous and accurate — this is the real measurement. Callers on a
    latency-sensitive path must use ``_cached_top_consumers`` instead. Roots may
    nest (``/opt`` and ``/opt/universal_agent``); results are de-duplicated by
    real path so a dir measured under two roots is reported once.
    """
    roots = roots if roots is not None else _consumer_roots()
    root_reals = {os.path.realpath(r) for r in roots}
    seen: Dict[str, Dict[str, Any]] = {}
    for root in roots:
        for child in _du_children(root):
            key = os.path.realpath(child["path"])
            # A child that is itself a root (/opt/universal_agent under /opt) is
            # scanned separately at finer granularity — reporting its aggregate
            # too would double-count it against its own children in the list.
            if key in root_reals:
                continue
            # Keep the larger reading if a path shows up under two roots.
            if key not in seen or child["size_bytes"] > seen[key]["size_bytes"]:
                seen[key] = child
    consumers = sorted(seen.values(), key=lambda c: c["size_bytes"], reverse=True)
    return consumers[:_SCAN_TOP_N]


def _refresh_top_consumers() -> None:
    """Re-measure in the background and publish the result. Never raises."""
    try:
        consumers = _top_consumers()
        with _scan_lock:
            _scan_state["consumers"] = consumers
            _scan_state["as_of"] = time.time()
    except Exception:  # noqa: BLE001 — a background scan must never crash the process
        logger.debug("disk_usage_health: background scan failed", exc_info=True)
    finally:
        with _scan_lock:
            _scan_state["refreshing"] = False


def _cached_top_consumers() -> tuple[List[Dict[str, Any]], Optional[float]]:
    """Return (consumers, age_seconds) without ever touching the filesystem.

    Serves the last measurement and kicks off a background refresh when it is
    older than the TTL. Returns ([], None) until the first scan lands. This is
    what keeps the probe ~1ms: `ops_proactive_health` is `async def` and awaits
    the probe on the gateway's event loop, so the probe must not walk 193G.
    """
    now = time.time()
    with _scan_lock:
        as_of = float(_scan_state["as_of"] or 0.0)
        consumers = list(_scan_state["consumers"])
        stale = (now - as_of) > _SCAN_TTL_S if as_of else True
        if stale and not _scan_state["refreshing"]:
            _scan_state["refreshing"] = True
            kick = True
        else:
            kick = False
    if kick:
        threading.Thread(
            target=_refresh_top_consumers,
            name="disk-health-scan",
            daemon=True,
        ).start()
    return consumers, (now - as_of if as_of else None)


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
        "echo '--- TRUE top consumers across the whole box (this is the one that "
        "matters: -x stays on one fs, and it SEES dot-dirs that `du -sh /home/ua/*` "
        "silently skips — that blindness hid 18G in ~/.cache on 2026-07-15) ---'; "
        "sudo du -x --max-depth=1 -h /opt/universal_agent /opt /home/ua /var/lib "
        "/tmp /root 2>/dev/null | sort -rh | head -20; "
        "echo '--- before deleting ANY of the above: does a live service back it? ---'; "
        "echo 'sudo grep -rl <path> /etc/systemd/system/*.service "
        "/home/ua/.config/systemd/user/*.service'; "
        "echo '--- regenerable .venv trees (rebuild: uv sync; NEVER delete the repo dir, "
        "some have no git remote) ---'; "
        "sudo du -sh $(find /home/ua/lrepos /opt/universal_agent/.worktrees -maxdepth 2 "
        "-name .venv -type d 2>/dev/null) 2>/dev/null | sort -rh | head -8; "
        "echo '--- model/tool caches (regenerable; re-download on demand) ---'; "
        "sudo du -sh /home/ua/.cache/huggingface/hub/* /home/ua/.npm 2>/dev/null "
        "| sort -rh | head -5; "
        "echo '--- docker: only the RECLAIMABLE column is free space ---'; "
        "sudo docker system df 2>/dev/null; "
        "echo '--- largest DB files (LIVE state — never blanket-delete) ---'; "
        "sudo ls -laSh /opt/universal_agent/*.db "
        "/opt/universal_agent/AGENT_RUN_WORKSPACES/*.db "
        "/var/lib/universal-agent/csi/*.db 2>/dev/null | head -6; "
        "echo '--- SAFE one-time reclaim (keeps running services) ---'; "
        "echo '# regenerable .venv/__pycache__/node_modules reap (runs daily; force now):'; "
        "echo 'cd /opt/universal_agent && sudo -u ua env "
        "PYTHONPATH=/opt/universal_agent/src .venv/bin/python -m "
        "universal_agent.scripts.vp_coder_regenerable_reaper'; "
        "echo '# whole-dir archival of completed missions (weekly tier):'; "
        "echo 'cd /opt/universal_agent && sudo -u ua env "
        "PYTHONPATH=/opt/universal_agent/src .venv/bin/python -m "
        "universal_agent.scripts.vp_coder_workspace_pruner'; "
        "echo '# docker dangling build cache only (system prune -a would remove LIVE images):'; "
        "echo 'sudo docker builder prune -f'"
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

    # Cached measurement + background refresh. Never touches the filesystem
    # here: this probe is awaited on the gateway event loop by
    # ops_proactive_health, so it must stay a pure syscall. Never raises.
    try:
        top_consumers, scan_age_s = _cached_top_consumers()
    except Exception:  # noqa: BLE001 — fail-open: a scan hiccup must not drop the finding
        logger.debug("disk_usage_health: top-consumer lookup failed", exc_info=True)
        top_consumers, scan_age_s = [], None

    if top_consumers:
        age_text = (
            f"measured {int((scan_age_s or 0) // 60)}m ago"
            if scan_age_s is not None
            else "just measured"
        )
        live_consumer_text = (
            f"Largest directories ({age_text}): "
            + ", ".join(f"{c['path']} ({c['size_gb']}G)" for c in top_consumers)
            + ". "
        )
    elif scan_age_s is None:
        live_consumer_text = (
            "Directory sizes are still being measured in the background (the "
            "first scan takes ~30s and lands on the next evaluation) — run the "
            "runbook for `du` now. "
        )
    else:
        # A scan ran and came back empty — say so plainly rather than implying a
        # measurement is still pending. Usually means the roots are absent here.
        live_consumer_text = (
            "Live top-consumer scan found nothing under the configured roots "
            "(absent on this host, or UA_DISK_HEALTH_ROOTS is misset) — run the "
            "runbook for `du`. "
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
        # Plain-English lead, built ONLY from what was measured. It deliberately
        # does not claim where the space went: the previous hardcoded version
        # asserted "most of the space is Docker images + running containers",
        # which was false (Docker was ~4G of 170G) and sent the operator to the
        # wrong place on 2026-07-15.
        "operator_summary": (
            f"The production server's disk is {worst_pct:.0f}% full "
            f"(about {worst_free_gb:.0f} GB free) and slowly filling up. Nothing "
            "is broken yet, but if it reaches ~100% the agent services start "
            "failing. "
            + (
                "The biggest directories right now are "
                + ", ".join(
                    f"{c['path']} ({c['size_gb']:.0f} GB)" for c in top_consumers[:3]
                )
                + " — most reclaimable space is caches and rebuildable .venv "
                "trees, but check each one backs no live service before deleting."
                if top_consumers
                else "Directory sizes are being measured now; the technical "
                "detail below has the runbook."
            )
        ),
        # Technical recommendation (for Claude / handoff). Leads with the
        # MEASURED largest dirs, then names reclaim CATEGORIES to check against
        # them. Deliberately asserts no "X is the driver" narrative: every such
        # claim previously hardcoded here went stale and misled the operator
        # (2026-07-15 — the alert blamed vp_coder while 18G sat in ~/.cache).
        "message": (
            f"Disk pressure on {len(pressured)} mount(s): {', '.join(mount_names)}. "
            f"Worst {worst_pct}%. {live_consumer_text}"
            "Judge reclaim from the measured list above, not from habit. "
            "Usually-regenerable categories: per-mission .venv / __pycache__ / "
            "node_modules under AGENT_RUN_WORKSPACES (daily "
            "scripts/vp_coder_regenerable_reaper.py + weekly "
            "scripts/vp_coder_workspace_pruner.py own the durable fix); "
            "model/tool caches (~/.cache/huggingface, ~/.npm, ~/.cache/uv); "
            "worktree and demo-repo .venv trees (rebuild via `uv sync`); "
            "dangling Docker layers (`docker builder prune`). "
            "Verify before deleting: a big dir may back a LIVE service — check "
            "`grep -rl <path> /etc/systemd/system/*.service "
            "~/.config/systemd/user/*.service` and prefer deleting a "
            "regenerable .venv over its parent. Run the runbook for live `du`."
        ),
        "severity_override": severity,
    }
