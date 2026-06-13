"""Proactive-activity control plane — per-process on/off for the ZAI dashboard.

The companion to ``services/zai_control`` (which governs the ZAI *rate* via tier
caps + pauses). This module governs which proactive *workloads* run at all: it
exposes a **hardcoded allowlist** of systemd units (the ZAI-consuming proactive
timers + continuous services) and a thin, fully-validated control surface so the
operator can flip each consumer on/off individually from the dashboard during a
controlled bring-up — without touching the core (gateway/api/webui) units.

Security model (this module shells ``systemctl`` on behalf of a web endpoint):

1. **Strict hardcoded allowlist.** Only the units in :data:`ALLOWLIST` can be
   controlled. The core / infra / self-immolation units in
   :data:`NEVER_CONTROLLABLE` are excluded by construction (a unit test asserts
   the two sets are disjoint and that the core units are not controllable).
2. **Action allowlist.** Only the benign systemctl verbs in
   :data:`ALLOWED_ACTIONS`. No unit-destroying verbs, ever.
3. **No shell.** Every control runs ``subprocess.run(["sudo","-n","systemctl",
   action, unit], ...)`` with an argv list — never a shell string. The unit is
   allowlist-validated and the action verb is allowlist-validated *before* the
   call, so even argv interpolation can't reach an arbitrary unit/verb.
4. **Read path needs no privilege.** ``systemctl show`` is unprivileged, so the
   state query runs without ``sudo``; only the mutating control uses ``sudo -n``
   (non-interactive — it fails fast rather than ever blocking on a password
   prompt). ``ua`` has passwordless sudo on the VPS.

Fail-soft: the state query degrades per-unit to ``"unknown"`` and never raises,
so a single missing/odd unit can't crash the dashboard. The control path raises
``ValueError`` for invalid input (the gateway maps that to HTTP 400) and returns
a structured result otherwise.

The §3c watchdog interaction: ``universal-agent-service-watchdog`` watches a
small core set that includes ``universal-agent-mission-control-sweeper`` — it
``systemctl restart``s any of them found inactive. So a plain ``stop`` of the
sweeper is undone by the watchdog once it is running. ``mask`` makes the
watchdog's ``restart`` *fail*, so the sweeper carries ``watchdog_guarded=True``
and its declarative ``off_actions`` are ``["stop","mask"]`` (``on_actions`` =
``["unmask","start"]``). The off/on *policy* lives here as metadata; the backend
control stays a single validated verb per call.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

# Per-call timeout for a systemctl CONTROL invocation (start/stop/…). A read is
# normally instant; a control can briefly block while the unit transitions.
SYSTEMCTL_TIMEOUT_SECONDS = 12.0
# Reads should be near-instant — keep them tight so a degraded systemd can't
# pile up the gateway thread pool (the whole activity list is one batched call).
SYSTEMCTL_READ_TIMEOUT_SECONDS = 6.0

# The benign systemctl verbs the panel may dispatch. Each maps 1:1 to
# ``sudo -n systemctl <action> <unit>``. No unit-destroying verbs.
ALLOWED_ACTIONS: frozenset[str] = frozenset(
    {"start", "stop", "restart", "enable", "disable", "mask", "unmask"}
)

# The units the service-watchdog (scripts/vps_service_watchdog.sh
# DEFAULT_SERVICE_SPECS) monitors and ``systemctl restart``s when found inactive.
# An ALLOWLISTED unit in this set is resurrected if merely stopped, so its "off"
# must ``mask`` (restart fails on a masked unit). Names are canonicalised to
# ``.service`` (the watchdog spec uses bare names, which systemd treats as
# ``.service``). Keep this in sync with that script — the guarded set below is
# DERIVED from it ∩ the allowlist, so adding any watched unit to the allowlist
# automatically makes it mask-guarded (no manual flag to forget).
WATCHDOG_WATCH_SET: frozenset[str] = frozenset(
    {
        "universal-agent-gateway.service",
        "universal-agent-api.service",
        "universal-agent-webui.service",
        "universal-agent-telegram.service",
        "universal-agent-mission-control-sweeper.service",
        "csi-ingester.service",
    }
)


def _activity(
    unit: str,
    label: str,
    group: str,
    *,
    heavy_zai: bool = False,
) -> dict[str, Any]:
    """One allowlist entry with its declarative off/on action policy.

    Watchdog-guarded units get a mask-based off so the service-watchdog can't
    resurrect an intentionally-stopped unit (see module docstring §3c)."""
    guarded = unit in WATCHDOG_WATCH_SET
    return {
        "unit": unit,
        "label": label,
        "group": group,
        "heavy_zai": heavy_zai,
        "watchdog_guarded": guarded,
        "off_actions": ["stop", "mask"] if guarded else ["stop"],
        "on_actions": ["unmask", "start"] if guarded else ["start"],
    }


# ── The allowlist — controllable PROACTIVE activities (verified installed) ───
# Group order is the display order. Core/infra units are deliberately ABSENT
# (see NEVER_CONTROLLABLE). Unit names are the exact installed names on the VPS.
_ALLOWLIST_ENTRIES: tuple[dict[str, Any], ...] = (
    # ── Timers: ZAI-consuming proactive work ────────────────────────────────
    _activity("universal-agent-csi-convergence-sync.timer", "CSI convergence sweep", "timers", heavy_zai=True),
    _activity("universal-agent-hourly-intel-digest.timer", "Hourly intel digest", "timers", heavy_zai=True),
    _activity("universal-agent-morning-briefing.timer", "Morning briefing", "timers"),
    _activity("universal-agent-evening-briefing.timer", "Evening briefing", "timers"),
    _activity("universal-agent-proactive-report-morning.timer", "Proactive report — morning", "timers"),
    _activity("universal-agent-proactive-report-midday.timer", "Proactive report — midday", "timers"),
    _activity("universal-agent-proactive-report-afternoon.timer", "Proactive report — afternoon", "timers"),
    _activity("universal-agent-proactive-demo-build-sweep.timer", "Demo-build sweep (Cody)", "timers", heavy_zai=True),
    _activity("universal-agent-intel-auto-promoter.timer", "Intel auto-promoter", "timers"),
    _activity("universal-agent-nightly-wiki.timer", "Nightly wiki (NotebookLM)", "timers", heavy_zai=True),
    _activity("universal-agent-csi-demo-triage-rank.timer", "CSI demo triage rank", "timers"),
    _activity("universal-agent-skill-gap-finder.timer", "Skill-gap finder", "timers"),
    _activity("universal-agent-backlog-triage.timer", "Backlog triage", "timers"),
    _activity("universal-agent-insight-scoring-health.timer", "Insight-scoring health", "timers"),
    _activity("universal-agent-proactive-signal-card-sync.timer", "Signal-card sync", "timers"),
    _activity("universal-agent-youtube-daily-digest.timer", "YouTube daily digest", "timers"),
    _activity("universal-agent-youtube-gold-channel-poller.timer", "YouTube gold-channel poller (CSI ingest)", "timers"),
    _activity("universal-agent-youtube-playlist-poller.timer", "YouTube playlist poller (CSI ingest)", "timers"),
    _activity("universal-agent-architecture-canvas-drift.timer", "Architecture-canvas drift", "timers"),
    _activity("universal-agent-vault-lint-contradictions.timer", "Vault lint (contradictions)", "timers"),
    _activity("universal-agent-proactive-artifact-digest.timer", "Proactive artifact digest", "timers"),
    _activity("universal-agent-codie-proactive-cleanup.timer", "Cody proactive cleanup", "timers"),
    _activity("universal-agent-artifact-reminders-sweep.timer", "Artifact reminders sweep", "timers"),
    # ── Continuous services ─────────────────────────────────────────────────
    _activity("universal-agent-mission-control-sweeper.service", "Mission Control sweeper (CoS)", "services", heavy_zai=True),
    _activity("ua-discord-intelligence.service", "Discord intelligence (relevance filter)", "services"),
    _activity("ua-discord-cc-bot.service", "Discord CC bot (bridge)", "services"),
    _activity("universal-agent-vp-worker@vp.coder.primary.service", "VP worker — Cody (coder)", "services", heavy_zai=True),
    _activity("universal-agent-vp-worker@vp.general.primary.service", "VP worker — Atlas (general)", "services", heavy_zai=True),
)

# Map for O(1) membership + metadata lookup, preserving declaration order.
ALLOWLIST: dict[str, dict[str, Any]] = {e["unit"]: e for e in _ALLOWLIST_ENTRIES}

# Derived (never hand-maintained): the allowlisted units the service-watchdog
# would resurrect, hence the ones whose "off" masks. Equals WATCHDOG_WATCH_SET ∩
# ALLOWLIST by construction (a unit test pins this invariant).
WATCHDOG_GUARDED_UNITS: frozenset[str] = frozenset(
    u for u, e in ALLOWLIST.items() if e["watchdog_guarded"]
)

# Core / infra / self-immolation units the panel must NEVER control. Listed for
# documentation + a disjointness guard test. The gateway/api/webui/docs units
# would kill the dashboard itself; the watchdog/oom/prune/health units are
# infra that should always run.
NEVER_CONTROLLABLE: frozenset[str] = frozenset(
    {
        "universal-agent-gateway.service",
        "universal-agent-api.service",
        "universal-agent-webui.service",
        "universal-agent-docs.service",
        "universal-agent-telegram.service",
        "universal-agent-service-watchdog.service",
        "universal-agent-service-watchdog.timer",
        "universal-agent-oom-alert.service",
        "universal-agent-oom-alert.timer",
        "universal-agent-uv-cache-prune.service",
        "universal-agent-uv-cache-prune.timer",
        "universal-agent-scratch-pruning.service",
        "universal-agent-scratch-pruning.timer",
        "universal-agent-vp-coder-workspace-pruning.service",
        "universal-agent-vp-coder-workspace-pruning.timer",
        "universal-agent-youtube-oauth-watchdog.service",
        "universal-agent-youtube-oauth-watchdog.timer",
        "universal-agent-proactive-health.service",
        "universal-agent-proactive-health.timer",
    }
)


def is_allowed_unit(unit: str) -> bool:
    """True iff ``unit`` is in the hardcoded controllable allowlist."""
    return unit in ALLOWLIST


def is_allowed_action(action: str) -> bool:
    """True iff ``action`` is one of the benign allowlisted systemctl verbs."""
    return action in ALLOWED_ACTIONS


# ── systemctl plumbing ──────────────────────────────────────────────────────


def _run_systemctl(argv: list[str], *, timeout: float = SYSTEMCTL_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    """Run a systemctl argv (never a shell string). Thin wrapper so tests can
    monkeypatch ``subprocess.run`` and assert the exact argv."""
    return subprocess.run(  # noqa: S603 — argv list, no shell; verbs+units allowlisted
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


# ``Id`` is requested so a batched multi-unit ``show`` can be keyed back to its
# unit. Properties are passed as repeated ``-p`` flags (the portable form;
# comma-joined to a single ``-p`` only works on newer systemd).
_SHOW_PROPS = (
    "Id",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "LastTriggerUSec",
    "NextElapseUSecRealtime",
    # Last-run exit result — used by the health column (zai_activity_health). For
    # a oneshot timer these are populated on the TRIGGERED service (the timer's
    # ``Unit=``, which may be a SHARED service, not the by-name sibling), not the
    # .timer; for a long-running service they describe the running process.
    "Result",
    "ExecMainStatus",
    "NRestarts",
    # ``Unit`` on a .timer is the service it activates (the report timers all
    # trigger ONE shared universal-agent-proactive-report.service, so the by-name
    # sibling would never run). ``ExecMainStartTimestampMonotonic`` is 0 for a
    # never-run-this-boot oneshot — the ONLY reliable never-ran signal, since
    # systemd defaults Result=success/ExecMainStatus=0 before the first run.
    "Unit",
    "ExecMainStartTimestampMonotonic",
)


def _prop_args() -> list[str]:
    args: list[str] = []
    for prop in _SHOW_PROPS:
        args += ["-p", prop]
    return args


def _parse_records(stdout: str) -> list[dict[str, str]]:
    """``systemctl show`` emits one ``KEY=value`` block per unit, blocks
    separated by a blank line. Returns one prop-dict per block."""
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in (stdout or "").splitlines():
        if not line.strip():
            if current:
                records.append(current)
                current = {}
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            current[k.strip()] = v.strip()
    if current:
        records.append(current)
    return records


def _build_state(unit: str, props: dict[str, str]) -> dict[str, Any]:
    """Combine the allowlist metadata + parsed systemctl props into the
    dashboard's per-unit shape. Pure; missing props degrade to ``unknown``."""
    meta = ALLOWLIST.get(unit, {})
    active = props.get("ActiveState", "unknown") or "unknown"
    ufs = props.get("UnitFileState", "unknown") or "unknown"
    return {
        "unit": unit,
        "label": meta.get("label", unit),
        "group": meta.get("group", "unknown"),
        "heavy_zai": bool(meta.get("heavy_zai")),
        "watchdog_guarded": bool(meta.get("watchdog_guarded")),
        "off_actions": list(meta.get("off_actions", ["stop"])),
        "on_actions": list(meta.get("on_actions", ["start"])),
        "active_state": active,
        "sub_state": props.get("SubState", "unknown") or "unknown",
        "unit_file_state": ufs,
        "is_active": active == "active",
        "is_enabled": ufs in {"enabled", "enabled-runtime", "static", "indirect", "alias", "generated"},
        "is_masked": ufs == "masked",
        "last_run": props.get("LastTriggerUSec", "") or "",
        "next_run": props.get("NextElapseUSecRealtime", "") or "",
        # Last-run exit signal (health column). For a service these describe the
        # running process; for a timer row they are empty (read the TRIGGERED
        # service named in ``triggers`` via get_last_run_results()).
        "result": props.get("Result", "") or "",
        "exec_main_status": props.get("ExecMainStatus", "") or "",
        "n_restarts": props.get("NRestarts", "") or "",
        # The service a .timer actually activates (its ``Unit=``); may be a shared
        # service. Empty for non-timer rows. The health resolver reads this unit's
        # last-run result, NOT a by-name sibling guess.
        "triggers": props.get("Unit", "") or "",
    }


def get_unit_state(unit: str) -> dict[str, Any]:
    """Live state for one allowlisted unit. FAILS SOFT: any error degrades to
    an ``unknown`` shape — never raises. Read-only (``systemctl show``, no sudo).
    """
    try:
        proc = _run_systemctl(
            ["systemctl", "show", unit, *_prop_args()],
            timeout=SYSTEMCTL_READ_TIMEOUT_SECONDS,
        )
        records = _parse_records(proc.stdout)
        props = records[0] if records else {}
    except Exception as exc:  # noqa: BLE001 — fail soft, per-unit
        logger.debug("zai_activity_control: state read failed for %s: %s", unit, exc)
        props = {}
    return _build_state(unit, props)


def _show_many(units: list[str]) -> dict[str, dict[str, Any]]:
    """Batched read: one ``systemctl show <u1> <u2> …`` for all units (keyed back
    by the requested ``Id``). FAILS SOFT to ``{}`` — the caller fills any missing
    unit with an ``unknown`` state. One subprocess instead of N keeps a degraded
    systemd from piling up the gateway thread pool."""
    if not units:
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        proc = _run_systemctl(
            ["systemctl", "show", *units, *_prop_args()],
            timeout=SYSTEMCTL_READ_TIMEOUT_SECONDS,
        )
        for rec in _parse_records(proc.stdout):
            uid = rec.get("Id", "")
            if uid in ALLOWLIST:
                out[uid] = _build_state(uid, rec)
    except Exception as exc:  # noqa: BLE001 — fail soft; caller fills unknowns
        logger.debug("zai_activity_control: batched state read failed: %s", exc)
    return out


def sibling_service_unit(unit: str) -> str:
    """``…-morning-briefing.timer`` -> ``…-morning-briefing.service``.

    Oneshot timers carry their last-run exit result on the *service* they trigger,
    not on the ``.timer`` unit. Identity for units already ending ``.service``."""
    if unit.endswith(".timer"):
        return unit[: -len(".timer")] + ".service"
    return unit


def get_last_run_results(units: list[str]) -> dict[str, dict[str, str]]:
    """Batched ``systemctl show`` of the given (sibling-service) units, returning
    the RAW prop dict per unit keyed by ``Id``.

    Unlike :func:`_show_many` this does NOT filter by the allowlist — the sibling
    ``.service`` of an allowlisted ``.timer`` is itself not allowlisted, yet its
    ``Result``/``ExecMainStatus`` is exactly the last-run health signal the health
    column needs. Read-only (no sudo). FAILS SOFT to ``{}``."""
    if not units:
        return {}
    out: dict[str, dict[str, str]] = {}
    try:
        proc = _run_systemctl(
            ["systemctl", "show", *units, *_prop_args()],
            timeout=SYSTEMCTL_READ_TIMEOUT_SECONDS,
        )
        for rec in _parse_records(proc.stdout):
            uid = rec.get("Id", "")
            if uid:
                out[uid] = rec
    except Exception as exc:  # noqa: BLE001 — fail soft; caller treats missing as no signal
        logger.debug("zai_activity_control: sibling-service result read failed: %s", exc)
    return out


def _inprocess_loops() -> list[dict[str, Any]]:
    """Read-only state of the in-process loops (heartbeat / cron). These are NOT
    systemd units — they are gated by Infisical flags read at gateway start, so
    the panel shows their state and a note; flipping them is an Infisical change
    + gateway restart, done out of band. Fails soft to a conservative shape."""
    items: list[dict[str, Any]] = []
    try:
        from universal_agent.feature_flags import cron_enabled, heartbeat_enabled

        items.append(
            {
                "key": "heartbeat",
                "label": "Heartbeat loop (Simone / proactive principals)",
                "env_var": "UA_DISABLE_HEARTBEAT",
                "enabled": bool(heartbeat_enabled()),
                "note": "Flip via Infisical UA_DISABLE_HEARTBEAT + gateway restart.",
            }
        )
        items.append(
            {
                "key": "cron",
                "label": "In-process cron scheduler",
                "env_var": "UA_DISABLE_CRON",
                "enabled": bool(cron_enabled()),
                "note": "Flip via Infisical UA_DISABLE_CRON + gateway restart.",
            }
        )
    except Exception as exc:  # noqa: BLE001 — fail soft
        logger.debug("zai_activity_control: inprocess loop read failed: %s", exc)
    return items


def list_activities() -> dict[str, Any]:
    """The full dashboard payload: per-unit live state for every allowlisted
    activity, grouped, plus the read-only in-process loop states. Never raises —
    each unit fails soft to ``unknown``."""
    states = _show_many(list(ALLOWLIST))
    units = [states.get(unit) or _build_state(unit, {}) for unit in ALLOWLIST]
    return {
        "actions_allowed": sorted(ALLOWED_ACTIONS),
        "groups": ["timers", "services"],
        "activities": units,
        "inprocess": _inprocess_loops(),
        "watchdog_guarded_units": sorted(WATCHDOG_GUARDED_UNITS),
    }


def control_unit(unit: str, action: str) -> dict[str, Any]:
    """Dispatch a single systemctl verb against one allowlisted unit.

    Re-validates the allowlist + action (belt-and-suspenders; the gateway also
    validates) and raises ``ValueError`` on bad input. Runs
    ``sudo -n systemctl <action> <unit>`` (argv list, no shell). Returns a
    structured result including the post-action state.
    """
    if not is_allowed_unit(unit):
        raise ValueError(f"Unit not in allowlist: {unit!r}")
    if not is_allowed_action(action):
        raise ValueError(f"Action not allowed: {action!r}")

    argv = ["sudo", "-n", "systemctl", action, unit]
    ok = False
    returncode: int | None = None
    stderr = ""
    try:
        proc = _run_systemctl(argv)
        returncode = proc.returncode
        ok = proc.returncode == 0
        stderr = (proc.stderr or "").strip()[-600:]
    except Exception as exc:  # noqa: BLE001 — return structured failure, don't raise
        logger.warning("zai_activity_control: %s %s failed: %s", action, unit, exc)
        stderr = f"{type(exc).__name__}: {exc}"[-600:]

    # Audit trail for a sudo-shelling mutation (success and failure).
    logger.info("zai_activity_control: dispatched action=%s unit=%s ok=%s rc=%s", action, unit, ok, returncode)

    after = get_unit_state(unit)
    return {
        "ok": ok,
        "unit": unit,
        "action": action,
        "returncode": returncode,
        "stderr": stderr,
        "is_active": after.get("is_active", False),
        "is_masked": after.get("is_masked", False),
        "state": after,
    }
