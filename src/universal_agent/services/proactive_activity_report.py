"""proactive_activity_report.py — autonomous-activity inventory for the proactive report.

Kevin cannot tell what his autonomous proactive processes are doing; they break
silently. This module makes them *legible*: it enumerates EVERY proactive
activity across BOTH schedulers and reports each one's health.

Why two schedulers (this is the load-bearing fact — get it wrong and the report
lies):

  * **systemd timers** run MOST proactive activities. They were migrated off the
    in-app cron, so ``cron_jobs.json`` shows them ``enabled=false`` — that is a
    MIGRATION ARTIFACT, NOT "off". Their real run-status lives in
    ``systemctl list-timers``.
  * **in-app CronService** still runs a handful (``simone_chat_auto_complete``,
    ``vp_mission_pr_reconciler``, ``paper_to_podcast_daily``,
    ``morning_ideation_report`` …) tracked in ``AGENT_RUN_WORKSPACES/cron_jobs.json``
    + ``cron_runs.jsonl``. ``claude_code_intel_sync`` is DELIBERATELY paused by
    the operator (X API credits depleted) — report it ⏸️ paused, not broken.
  * **lanes** are not crons at all — their health is DB freshness (convergence
    candidates, CSI events per source, VP missions). ``hackernews`` and
    ``claude_code_intel`` are intentionally parked.

Design contract: pure-Python, deterministic, and ``build_activity_inventory``
NEVER raises — every source is wrapped so a missing systemctl (dev box), a
missing registry file, or a locked DB degrades to ``status="unknown"`` rather
than breaking the report.

Public API:
  * ``build_activity_inventory(conn) -> dict``  — the reconciled inventory.
  * ``render_activity_section(inventory) -> str`` — the compact text LEAD section.
  * ``capture_activity_report_to_memory(inventory, section_text) -> dict`` —
    persist the section into Simone's durable shared memory.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import re
import sqlite3
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status vocabulary (single source so render + classify never drift)
# ---------------------------------------------------------------------------
STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"   # ran but stale / last attempt failed / retrying
STATUS_PAUSED = "paused"       # off by deliberate operator decision
STATUS_DARK = "dark"           # never ran / no data and not intentionally parked
STATUS_PARKED = "parked"       # intentionally not running (e.g. hackernews)
STATUS_UNKNOWN = "unknown"     # could not determine (systemctl/DB unavailable)

_ICON = {
    STATUS_HEALTHY: "✅",
    STATUS_DEGRADED: "⚠️",
    STATUS_PAUSED: "⏸️",
    STATUS_PARKED: "⏸️",
    STATUS_DARK: "🌑",
    STATUS_UNKNOWN: "❔",
}

# Activities deliberately paused by the operator (NOT broken). Keyed by the
# stable in-app system_job id.
_OPERATOR_PAUSED_CRONS = {
    "claude_code_intel_sync": "X API credits depleted (operator-paused)",
}

# Lanes that are intentionally parked — fresh data is NOT expected, so absence
# is reported ⏸️/parked rather than 🌑/dark.
_PARKED_LANE_SOURCES = {"hackernews", "claude_code_intel"}

# Expected cadence (seconds) for systemd timers whose period cannot be inferred
# from a single NEXT-LAST observation (e.g. weekly/monthly units, or units that
# have only fired once). Keyed by the timer unit basename (without the
# ``universal-agent-`` prefix / ``.timer`` suffix). A unit absent from this map
# falls back to the observed NEXT-LAST delta with a generous multiplier.
_SYSTEMD_EXPECTED_PERIOD_SECONDS = {
    "service-watchdog": 60,
    "oom-alert": 60,
    "proactive-health": 5 * 60,
    "proactive-signal-card-sync": 60 * 60,
    "artifact-reminders-sweep": 30 * 60,
    "csi-convergence-sync": 60 * 60,
    "hourly-intel-digest": 60 * 60,
    "morning-briefing": 24 * 60 * 60,
    "evening-briefing": 24 * 60 * 60,
    "proactive-report-morning": 24 * 60 * 60,
    "proactive-report-midday": 24 * 60 * 60,
    "proactive-report-afternoon": 24 * 60 * 60,
    "nightly-wiki": 24 * 60 * 60,
    "youtube-gold-channel-poller": 24 * 60 * 60,
    "youtube-daily-digest": 24 * 60 * 60,
    "youtube-oauth-watchdog": 24 * 60 * 60,
    "intel-auto-promoter": 24 * 60 * 60,
    "csi-demo-triage-rank": 24 * 60 * 60,
    "scratch-pruning": 24 * 60 * 60,
    "codie-proactive-cleanup": 24 * 60 * 60,
    "proactive-demo-build-sweep": 24 * 60 * 60,
    "backlog-triage": 24 * 60 * 60,
    "proactive-artifact-digest": 24 * 60 * 60,
    "session-reaper": 24 * 60 * 60,
    "uv-cache-prune": 24 * 60 * 60,
    "skill-gap-finder": 7 * 24 * 60 * 60,
    "vp-coder-workspace-pruning": 7 * 24 * 60 * 60,
    "architecture-canvas-drift": 7 * 24 * 60 * 60,
    "vault-lint-contradictions": 30 * 24 * 60 * 60,
}

# Category grouping for the rendered section (unit basename → category).
_SYSTEMD_CATEGORY = {
    "morning-briefing": "Briefings",
    "evening-briefing": "Briefings",
    "proactive-report-morning": "Briefings",
    "proactive-report-midday": "Briefings",
    "proactive-report-afternoon": "Briefings",
    "hourly-intel-digest": "Intelligence",
    "csi-convergence-sync": "Intelligence",
    "intel-auto-promoter": "Intelligence",
    "csi-demo-triage-rank": "Intelligence",
    "proactive-signal-card-sync": "Intelligence",
    "nightly-wiki": "Intelligence",
    "skill-gap-finder": "Intelligence",
    "backlog-triage": "Intelligence",
    "youtube-gold-channel-poller": "YouTube",
    "youtube-daily-digest": "YouTube",
    "youtube-oauth-watchdog": "YouTube",
    "proactive-artifact-digest": "Artifacts",
    "artifact-reminders-sweep": "Artifacts",
    "scratch-pruning": "Artifacts",
    "codie-proactive-cleanup": "Demos & Code",
    "proactive-demo-build-sweep": "Demos & Code",
    "vp-coder-workspace-pruning": "Demos & Code",
    "vault-lint-contradictions": "Vault & Docs",
    "architecture-canvas-drift": "Vault & Docs",
    "service-watchdog": "Infra & Health",
    "oom-alert": "Infra & Health",
    "proactive-health": "Infra & Health",
    "session-reaper": "Infra & Health",
    "uv-cache-prune": "Infra & Health",
}

# In-app cron system_job id → (display name, category). Only the activities that
# still run via the in-app CronService (enabled=true) plus the deliberately
# paused claude_code_intel_sync belong here; everything else with a hashed id is
# a systemd-migration artifact and is surfaced via the systemd path.
_INAPP_CRON_ACTIVITIES = {
    "simone_chat_auto_complete": ("Simone chat auto-complete", "Orchestration"),
    "vp_mission_pr_reconciler": ("VP mission ↔ PR reconciler", "Orchestration"),
    "paper_to_podcast_daily": ("Paper-to-podcast (daily)", "Intelligence"),
    "morning_ideation_report": ("Morning ideation report", "Briefings"),
    "claude_code_intel_sync": ("Claude Code intel sync", "Intelligence"),
}

_CATEGORY_ORDER = [
    "Briefings",
    "Intelligence",
    "YouTube",
    "Artifacts",
    "Demos & Code",
    "Orchestration",
    "Lanes",
    "Vault & Docs",
    "Infra & Health",
    "Other",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    """Parse a variety of ISO-ish timestamps seen across UA DBs to aware UTC."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Normalize trailing Z and zero-pad offsets like +0000 → +00:00
    s = s.replace("Z", "+00:00")
    m = re.search(r"([+-]\d{2})(\d{2})$", s)
    if m:
        s = s[: m.start()] + f"{m.group(1)}:{m.group(2)}"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_seconds(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    return (_now() - dt).total_seconds()


def _humanize_age(dt: datetime | None) -> str:
    if dt is None:
        return "never"
    secs = _age_seconds(dt) or 0
    if secs < 0:
        secs = 0
    if secs < 90:
        return f"{int(secs)}s ago"
    if secs < 90 * 60:
        return f"{int(secs // 60)}m ago"
    if secs < 36 * 3600:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def _workspaces_dir() -> str:
    """Resolve AGENT_RUN_WORKSPACES the same way the rest of the codebase does."""
    env = os.getenv("AGENT_RUN_WORKSPACES_DIR")
    if env:
        return env
    # Same parent dir as the activity DB (…/AGENT_RUN_WORKSPACES/activity_state.db).
    try:
        from universal_agent.durable.db import get_activity_db_path

        return os.path.dirname(get_activity_db_path())
    except Exception:
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        )
        return os.path.join(repo_root, "AGENT_RUN_WORKSPACES")


# ---------------------------------------------------------------------------
# Source 1: systemd timers
# ---------------------------------------------------------------------------

def _unit_basename(unit: str) -> str:
    """``universal-agent-proactive-health.timer`` → ``proactive-health``."""
    name = unit.strip()
    if name.endswith(".timer"):
        name = name[: -len(".timer")]
    if name.startswith("universal-agent-"):
        name = name[len("universal-agent-"):]
    return name


def _parse_systemd_timers(raw: str) -> list[dict[str, Any]]:
    """Parse ``systemctl list-timers --all`` output for universal-agent units.

    The table is whitespace-aligned with a header:
        NEXT  LEFT  LAST  PASSED  UNIT  ACTIVATES
    NEXT/LAST are date strings containing spaces, so we anchor on the UNIT
    token (``universal-agent-*.timer``) and parse outward from it.
    """
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.rstrip()
        if "universal-agent-" not in line or ".timer" not in line:
            continue
        m = re.search(r"(universal-agent-[\w.-]+\.timer)", line)
        if not m:
            continue
        unit = m.group(1)
        pre = line[: m.start()]
        # pre == "NEXT  LEFT  LAST  PASSED". Column boundaries are unreliable
        # because LEFT/PASSED carry single-space values ("1min 21s", "8min ago"),
        # so we extract the embedded full datetimes directly. systemd renders
        # both NEXT and LAST as "YYYY-MM-DD HH:MM:SS TZ". The FIRST is NEXT, the
        # SECOND is LAST. A "-" in place of NEXT means no next scheduled → the
        # single datetime found is LAST.
        dt_re = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\s+\w+)?")
        found = dt_re.findall(pre)
        next_dt = None
        last_dt = None
        # When two datetimes are present the FIRST is NEXT and the SECOND is
        # LAST. When only one is present it is LAST (a fired timer always has a
        # LAST; NEXT can be "-"/"n/a" for an expired or one-shot unit). The
        # leading sentinel ("-" or "n/a" before the first datetime) also marks a
        # missing NEXT.
        no_next = bool(re.match(r"\s*(?:-|n/a)\b", pre, re.IGNORECASE))
        if len(found) >= 2 and not no_next:
            next_dt = _parse_systemd_dt(found[0])
            last_dt = _parse_systemd_dt(found[1])
        elif len(found) == 1:
            last_dt = _parse_systemd_dt(found[0])
        elif len(found) >= 2:
            # no_next sentinel but two datetimes — treat both conservatively:
            # the later (max) is LAST, the other NEXT.
            d0 = _parse_systemd_dt(found[0])
            d1 = _parse_systemd_dt(found[1])
            last_dt = max(d0, d1) if d0 and d1 else (d0 or d1)
            next_dt = min(d0, d1) if d0 and d1 else None
        rows.append(
            {
                "unit": unit,
                "basename": _unit_basename(unit),
                "next_dt": next_dt,
                "last_dt": last_dt,
            }
        )
    return rows


def _parse_systemd_dt(value: str) -> datetime | None:
    """Parse a systemd timestamp like ``Mon 2026-06-22 01:22:04 UTC``."""
    if not value or value.strip() in ("-", "n/a"):
        return None
    s = value.strip()
    # Drop leading weekday if present.
    parts = s.split()
    if parts and re.fullmatch(r"[A-Za-z]{3}", parts[0]):
        parts = parts[1:]
    if len(parts) < 2:
        return None
    date_part, time_part = parts[0], parts[1]
    try:
        dt = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    # systemd renders in the unit's display TZ; UA timers are configured UTC and
    # list-timers prints UTC on the VPS. Treat a non-UTC label conservatively as
    # UTC (we only ever compare ages, and a few hours of skew never flips a
    # daily/hourly health verdict).
    return dt.replace(tzinfo=timezone.utc)


def _classify_systemd(row: dict[str, Any]) -> tuple[str, str]:
    """Return (status, detail) for a parsed systemd-timer row."""
    last_dt = row.get("last_dt")
    next_dt = row.get("next_dt")
    base = row.get("basename", "")

    if last_dt is None:
        # Never fired. If it has a scheduled NEXT it is simply pending its first
        # window (e.g. just-deployed), not broken.
        if next_dt is not None:
            return STATUS_HEALTHY, f"not yet run; next {_humanize_age(next_dt)[:-4] or 'soon'}".replace(" ago", "")
        return STATUS_DARK, "never run, no next scheduled"

    age = _age_seconds(last_dt) or 0.0
    expected = _SYSTEMD_EXPECTED_PERIOD_SECONDS.get(base)
    if expected is None and next_dt is not None:
        # Infer period from the NEXT-LAST gap.
        gap = (next_dt - last_dt).total_seconds()
        if gap > 0:
            expected = gap
    if expected is None:
        expected = 24 * 60 * 60  # conservative default

    # Healthy if the last run is within ~2× the expected period (one missed
    # window tolerated for scheduling jitter / deploy restarts).
    if age <= expected * 2.0:
        return STATUS_HEALTHY, f"last {_humanize_age(last_dt)}"
    return STATUS_DEGRADED, f"last {_humanize_age(last_dt)} (overdue, ~{_fmt_period(expected)} cadence)"


def _fmt_period(secs: float) -> str:
    if secs < 90 * 60:
        return f"{int(round(secs / 60))}m"
    if secs < 36 * 3600:
        return f"{int(round(secs / 3600))}h"
    return f"{int(round(secs / 86400))}d"


def _collect_systemd_activities() -> list[dict[str, Any]]:
    try:
        proc = subprocess.run(
            ["systemctl", "list-timers", "--all", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # FileNotFoundError on dev, timeouts, etc.
        logger.debug("systemctl list-timers unavailable: %s", exc)
        return [
            {
                "name": "systemd-timers",
                "category": "Infra & Health",
                "scheduler": "systemd",
                "cadence": "various",
                "last_run_iso": None,
                "status": STATUS_UNKNOWN,
                "detail": "systemctl unavailable (dev box)",
            }
        ]

    if proc.returncode != 0:
        return [
            {
                "name": "systemd-timers",
                "category": "Infra & Health",
                "scheduler": "systemd",
                "cadence": "various",
                "last_run_iso": None,
                "status": STATUS_UNKNOWN,
                "detail": f"systemctl rc={proc.returncode}",
            }
        ]

    activities: list[dict[str, Any]] = []
    for row in _parse_systemd_timers(proc.stdout):
        base = row["basename"]
        status, detail = _classify_systemd(row)
        period = _SYSTEMD_EXPECTED_PERIOD_SECONDS.get(base)
        activities.append(
            {
                "name": base,
                "category": _SYSTEMD_CATEGORY.get(base, "Other"),
                "scheduler": "systemd",
                "cadence": _fmt_period(period) if period else "scheduled",
                "last_run_iso": _iso(row.get("last_dt")),
                "status": status,
                "detail": detail,
            }
        )
    return activities


# ---------------------------------------------------------------------------
# Source 2: in-app CronService
# ---------------------------------------------------------------------------

def _load_cron_registry(workspaces_dir: str) -> list[dict[str, Any]]:
    import json

    path = os.path.join(workspaces_dir, "cron_jobs.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return list(json.load(fh).get("jobs", []))
    except Exception as exc:
        logger.debug("cron_jobs.json unavailable (%s): %s", path, exc)
        return []


def _load_cron_runs(workspaces_dir: str, limit: int = 4000) -> dict[str, dict[str, Any]]:
    """Return the most recent run record per job_id from cron_runs.jsonl."""
    import json

    path = os.path.join(workspaces_dir, "cron_runs.jsonl")
    latest: dict[str, dict[str, Any]] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception as exc:
        logger.debug("cron_runs.jsonl unavailable (%s): %s", path, exc)
        return latest
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        jid = rec.get("job_id")
        if not jid:
            continue
        # File order is chronological; later wins.
        latest[jid] = rec
    return latest


def _system_job_id(job: dict[str, Any]) -> str:
    md = job.get("metadata") or {}
    return str(md.get("system_job") or md.get("system_job_id") or job.get("job_id") or "")


def _collect_inapp_cron_activities(workspaces_dir: str) -> list[dict[str, Any]]:
    jobs = _load_cron_registry(workspaces_dir)
    if not jobs:
        # Degrade: emit one unknown placeholder per known in-app activity so the
        # inventory stays complete even when the registry can't be read.
        return [
            {
                "name": disp,
                "category": cat,
                "scheduler": "in-app cron",
                "cadence": "?",
                "last_run_iso": None,
                "status": STATUS_UNKNOWN,
                "detail": "cron_jobs.json unavailable",
            }
            for (disp, cat) in _INAPP_CRON_ACTIVITIES.values()
        ]

    runs = _load_cron_runs(workspaces_dir)
    by_sysjob: dict[str, dict[str, Any]] = {}
    for job in jobs:
        by_sysjob[_system_job_id(job)] = job

    activities: list[dict[str, Any]] = []
    for sysjob, (disp, cat) in _INAPP_CRON_ACTIVITIES.items():
        job = by_sysjob.get(sysjob)
        cadence = (job or {}).get("cron_expr") or (
            f"every {(job or {}).get('every_seconds')}s" if (job or {}).get("every_seconds") else "?"
        )
        run = runs.get((job or {}).get("job_id", "")) if job else None
        last_dt = None
        if job and job.get("last_run_at"):
            try:
                last_dt = datetime.fromtimestamp(float(job["last_run_at"]), tz=timezone.utc)
            except Exception:
                last_dt = None
        if run and run.get("started_at"):
            try:
                run_dt = datetime.fromtimestamp(float(run["started_at"]), tz=timezone.utc)
                if last_dt is None or run_dt > last_dt:
                    last_dt = run_dt
            except Exception:
                pass

        status, detail = _classify_inapp(sysjob, job, run, last_dt)
        activities.append(
            {
                "name": disp,
                "category": cat,
                "scheduler": "in-app cron",
                "cadence": str(cadence),
                "last_run_iso": _iso(last_dt),
                "status": status,
                "detail": detail,
            }
        )
    return activities


def _classify_inapp(
    sysjob: str,
    job: dict[str, Any] | None,
    run: dict[str, Any] | None,
    last_dt: datetime | None,
) -> tuple[str, str]:
    # Deliberate operator pause wins over everything.
    if sysjob in _OPERATOR_PAUSED_CRONS:
        return STATUS_PAUSED, _OPERATOR_PAUSED_CRONS[sysjob]

    if job is None:
        return STATUS_UNKNOWN, "not present in cron_jobs.json"

    if not job.get("enabled", True):
        # Disabled in-app and not a known operator pause: treat as paused with a
        # generic note rather than "broken" — disabling is a deliberate flip.
        return STATUS_PAUSED, "disabled in cron_jobs.json"

    # Last run outcome.
    run_status = str((run or {}).get("status") or "").strip().lower()
    if run_status in ("failed", "error", "retry_queued", "timeout"):
        return STATUS_DEGRADED, f"last run {run_status} ({_humanize_age(last_dt)})"

    if last_dt is None:
        return STATUS_DARK, "enabled but no run recorded"

    # Cadence-aware freshness for the high-frequency ones; daily ones get a 2-day
    # tolerance window.
    age = _age_seconds(last_dt) or 0.0
    every = float(job.get("every_seconds") or 0)
    if every > 0:
        if age <= every * 3:
            return STATUS_HEALTHY, f"last {_humanize_age(last_dt)}"
        return STATUS_DEGRADED, f"last {_humanize_age(last_dt)} (overdue)"
    # cron_expr daily/weekly job — tolerate ~36h.
    if age <= 36 * 3600:
        return STATUS_HEALTHY, f"last {_humanize_age(last_dt)}"
    return STATUS_DEGRADED, f"last {_humanize_age(last_dt)} (overdue)"


# ---------------------------------------------------------------------------
# Source 3: lanes (DB freshness)
# ---------------------------------------------------------------------------

def _lane_status(last_dt: datetime | None, fresh_window_secs: float, parked: bool) -> tuple[str, str]:
    if last_dt is None:
        return (STATUS_PARKED if parked else STATUS_DARK), ("intentionally parked" if parked else "no records")
    age = _age_seconds(last_dt) or 0.0
    if age <= fresh_window_secs:
        return STATUS_HEALTHY, f"last produced {_humanize_age(last_dt)}"
    if parked:
        return STATUS_PARKED, f"parked (last {_humanize_age(last_dt)})"
    return STATUS_DARK, f"stale — last produced {_humanize_age(last_dt)}"


def _collect_lane_activities(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    activities: list[dict[str, Any]] = []

    # --- Convergence pipeline (activity_state.db / the report's own conn) ---
    try:
        row = conn.execute(
            "SELECT MAX(created_at) AS m FROM convergence_candidates"
        ).fetchone()
        last_dt = _parse_iso(row["m"] if row else None)
        status, detail = _lane_status(last_dt, fresh_window_secs=6 * 3600, parked=False)
        activities.append(
            {
                "name": "Convergence pipeline",
                "category": "Lanes",
                "scheduler": "lane",
                "cadence": "continuous",
                "last_run_iso": _iso(last_dt),
                "status": status,
                "detail": detail,
            }
        )
    except Exception as exc:
        activities.append(_unknown_lane("Convergence pipeline", exc))

    # --- VP missions (vp_state.db) ---
    try:
        from universal_agent.durable.db import get_vp_db_path

        vp_conn = sqlite3.connect(f"file:{get_vp_db_path()}?mode=ro", uri=True, timeout=5)
        vp_conn.row_factory = sqlite3.Row
        try:
            running = vp_conn.execute(
                "SELECT COUNT(*) AS c FROM vp_missions WHERE status='running'"
            ).fetchone()
            recent = vp_conn.execute(
                "SELECT MAX(updated_at) AS m FROM vp_missions WHERE status IN ('running','completed')"
            ).fetchone()
        finally:
            vp_conn.close()
        last_dt = _parse_iso(recent["m"] if recent else None)
        run_n = int(running["c"]) if running else 0
        status, detail = _lane_status(last_dt, fresh_window_secs=12 * 3600, parked=False)
        detail = f"{detail}; {run_n} running"
        activities.append(
            {
                "name": "VP missions",
                "category": "Lanes",
                "scheduler": "lane",
                "cadence": "continuous",
                "last_run_iso": _iso(last_dt),
                "status": status,
                "detail": detail,
            }
        )
    except Exception as exc:
        activities.append(_unknown_lane("VP missions", exc))

    # --- CSI events per source (canonical csi.db) ---
    try:
        from universal_agent.services.transcript_corpus import resolve_csi_db_path

        csi_path = resolve_csi_db_path()
        csi_conn = sqlite3.connect(f"file:{csi_path}?mode=ro", uri=True, timeout=5)
        csi_conn.row_factory = sqlite3.Row
        try:
            rows = csi_conn.execute(
                "SELECT source, MAX(occurred_at) AS m, COUNT(*) AS c "
                "FROM events GROUP BY source ORDER BY 2 DESC"
            ).fetchall()
        finally:
            csi_conn.close()
        if not rows:
            activities.append(
                {
                    "name": "CSI events",
                    "category": "Lanes",
                    "scheduler": "lane",
                    "cadence": "continuous",
                    "last_run_iso": None,
                    "status": STATUS_DARK,
                    "detail": "no CSI events found",
                }
            )
        for r in rows:
            source = str(r["source"] or "unknown")
            last_dt = _parse_iso(r["m"])
            parked = any(p in source.lower() for p in _PARKED_LANE_SOURCES)
            status, detail = _lane_status(last_dt, fresh_window_secs=6 * 3600, parked=parked)
            activities.append(
                {
                    "name": f"CSI · {source}",
                    "category": "Lanes",
                    "scheduler": "lane",
                    "cadence": "continuous",
                    "last_run_iso": _iso(last_dt),
                    "status": status,
                    "detail": detail,
                }
            )
    except Exception as exc:
        activities.append(_unknown_lane("CSI events", exc))

    # --- Proactive artifacts flow (csi.db proactive_artifacts) ---
    try:
        from universal_agent.services.transcript_corpus import resolve_csi_db_path

        csi_path = resolve_csi_db_path()
        a_conn = sqlite3.connect(f"file:{csi_path}?mode=ro", uri=True, timeout=5)
        a_conn.row_factory = sqlite3.Row
        try:
            row = a_conn.execute(
                "SELECT MAX(COALESCE(created_at, '')) AS m, COUNT(*) AS c FROM proactive_artifacts"
            ).fetchone()
        finally:
            a_conn.close()
        last_dt = _parse_iso(row["m"] if row else None)
        status, detail = _lane_status(last_dt, fresh_window_secs=48 * 3600, parked=False)
        activities.append(
            {
                "name": "Proactive artifacts",
                "category": "Lanes",
                "scheduler": "lane",
                "cadence": "continuous",
                "last_run_iso": _iso(last_dt),
                "status": status,
                "detail": detail,
            }
        )
    except Exception as exc:
        activities.append(_unknown_lane("Proactive artifacts", exc))

    return activities


def _unknown_lane(name: str, exc: Exception) -> dict[str, Any]:
    logger.debug("lane %s unavailable: %s", name, exc)
    return {
        "name": name,
        "category": "Lanes",
        "scheduler": "lane",
        "cadence": "continuous",
        "last_run_iso": None,
        "status": STATUS_UNKNOWN,
        "detail": "DB unavailable",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_activity_inventory(conn: sqlite3.Connection) -> dict[str, Any]:
    """Reconcile systemd timers + in-app crons + lane DB freshness into one inventory.

    Returns::

        {
          "generated_at": iso,
          "activities": [
            {"name","category","scheduler","cadence","last_run_iso","status","detail"},
            ...
          ],
          "summary": {"healthy","degraded","paused","dark","total"},
        }

    NEVER raises — every source is independently guarded and degrades to
    ``status="unknown"`` entries on failure.
    """
    activities: list[dict[str, Any]] = []

    try:
        activities.extend(_collect_systemd_activities())
    except Exception as exc:  # pragma: no cover — defensive backstop
        logger.warning("systemd activity collection failed: %s", exc)

    try:
        activities.extend(_collect_inapp_cron_activities(_workspaces_dir()))
    except Exception as exc:  # pragma: no cover
        logger.warning("in-app cron activity collection failed: %s", exc)

    try:
        activities.extend(_collect_lane_activities(conn))
    except Exception as exc:  # pragma: no cover
        logger.warning("lane activity collection failed: %s", exc)

    summary = {
        "healthy": sum(1 for a in activities if a["status"] == STATUS_HEALTHY),
        "degraded": sum(1 for a in activities if a["status"] == STATUS_DEGRADED),
        "paused": sum(1 for a in activities if a["status"] in (STATUS_PAUSED, STATUS_PARKED)),
        "dark": sum(1 for a in activities if a["status"] == STATUS_DARK),
        "unknown": sum(1 for a in activities if a["status"] == STATUS_UNKNOWN),
        "total": len(activities),
    }

    return {
        "generated_at": _iso(_now()),
        "activities": activities,
        "summary": summary,
    }


def render_activity_section(inventory: dict[str, Any]) -> str:
    """Render the compact text LEAD section, grouped by category.

    One line per activity: ``<icon> <name> — <detail>``, preceded by a one-line
    summary header.
    """
    activities = inventory.get("activities", []) if isinstance(inventory, dict) else []
    summary = inventory.get("summary", {}) if isinstance(inventory, dict) else {}

    header = (
        f"{summary.get('healthy', 0)} healthy · "
        f"{summary.get('degraded', 0)} degraded · "
        f"{summary.get('paused', 0)} paused/parked · "
        f"{summary.get('dark', 0)} dark"
    )
    if summary.get("unknown"):
        header += f" · {summary.get('unknown', 0)} unknown"

    lines = [
        f"Proactive Activity Status — {header}",
        f"({summary.get('total', len(activities))} autonomous activities across systemd timers, in-app crons, and lanes)",
        "",
    ]

    # Group by category in a stable order.
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for a in activities:
        by_cat.setdefault(a.get("category", "Other"), []).append(a)

    ordered_cats = [c for c in _CATEGORY_ORDER if c in by_cat]
    ordered_cats += [c for c in sorted(by_cat) if c not in _CATEGORY_ORDER]

    for cat in ordered_cats:
        lines.append(f"── {cat} ──")
        for a in sorted(by_cat[cat], key=lambda x: str(x.get("name", ""))):
            icon = _ICON.get(a.get("status", STATUS_UNKNOWN), "❔")
            name = a.get("name", "?")
            detail = a.get("detail", "")
            lines.append(f"  {icon} {name} — {detail}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _degraded_dark_paused_summary(inventory: dict[str, Any]) -> str:
    """One-paragraph natural-language summary naming the non-healthy activities."""
    activities = inventory.get("activities", []) if isinstance(inventory, dict) else []
    summary = inventory.get("summary", {}) if isinstance(inventory, dict) else {}

    def names(*statuses: str) -> list[str]:
        return [str(a.get("name")) for a in activities if a.get("status") in statuses]

    degraded = names(STATUS_DEGRADED)
    dark = names(STATUS_DARK)
    paused = names(STATUS_PAUSED, STATUS_PARKED)
    unknown = names(STATUS_UNKNOWN)

    parts = [
        f"Proactive activity inventory: {summary.get('healthy', 0)} healthy, "
        f"{summary.get('degraded', 0)} degraded, {summary.get('paused', 0)} paused/parked, "
        f"{summary.get('dark', 0)} dark of {summary.get('total', len(activities))} total."
    ]
    if degraded:
        parts.append("DEGRADED/overdue: " + ", ".join(degraded) + ".")
    if dark:
        parts.append("DARK (no recent output): " + ", ".join(dark) + ".")
    if paused:
        parts.append("Paused/parked by design: " + ", ".join(paused) + ".")
    if unknown:
        parts.append("Could not classify: " + ", ".join(unknown) + ".")
    if not (degraded or dark):
        parts.append("All non-paused proactive activities are running on cadence.")
    return " ".join(parts)


def capture_activity_report_to_memory(
    inventory: dict[str, Any],
    section_text: str,
) -> dict[str, Any]:
    """Persist the activity section into Simone's durable shared memory.

    Best-effort: any failure (memory disabled, orchestrator import error, write
    failure) is swallowed and reported in the return dict rather than raised, so
    it can never break report delivery.

    Production runs ``UA_MEMORY_ROLLOVER_MODE=transcript`` (the default), under
    which ``capture_session_rollover`` no-ops on a summary alone. To make the
    capture land regardless of mode we write the section to a transient run-log
    file and pass it as ``run_log_path`` (picked up under transcript mode) while
    also passing the natural-language ``summary`` (used under summary_only mode).
    """
    try:
        from universal_agent.feature_flags import memory_enabled

        if not memory_enabled(default=True):
            return {"captured": False, "reason": "memory_disabled"}

        from universal_agent.memory.orchestrator import get_memory_orchestrator
        from universal_agent.memory.paths import resolve_shared_memory_workspace

        summary = _degraded_dark_paused_summary(inventory)

        # Stage the full section as a run-log so transcript-mode rollover has
        # real content to capture. Best-effort tempfile; absence just means we
        # fall back to summary text under summary_only mode.
        run_log_path: str | None = None
        try:
            import tempfile

            fd, run_log_path = tempfile.mkstemp(prefix="proactive_activity_", suffix=".log")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(summary + "\n\n" + (section_text or ""))
        except Exception:
            run_log_path = None

        orch = get_memory_orchestrator(workspace_dir=resolve_shared_memory_workspace())
        result = orch.capture_session_rollover(
            session_id="proactive_activity_report",
            trigger="proactive_activity_report",
            run_log_path=run_log_path,
            summary=summary,
        )

        if run_log_path:
            try:
                os.unlink(run_log_path)
            except OSError:
                pass

        return result if isinstance(result, dict) else {"captured": True}
    except Exception as exc:  # noqa: BLE001 — never break the report
        logger.debug("capture_activity_report_to_memory failed: %s", exc)
        return {"captured": False, "reason": f"error: {exc}"}
