"""Cron scheduler liveness invariants — catch a wedged scheduler in minutes.

Motivation (top-9 handoff, task 5): a wedged in-app cron scheduler previously
went ~20h undetected. ``cron_staleness`` keys off ``last_run_at`` with a
threshold of 2× the schedule's longest gap — for a DAILY cron that means a
dead scheduler is flagged only after ~48h. These two invariants close that
gap from the other side:

1. ``cron_loop_liveness`` — the scheduler's own promise. Every enabled
   in-app cron carries ``next_run_at``; if ``now`` is past it by more than a
   cadence-scaled grace (floor 3 min for every-minute crons, cap 2h for
   daily/weekly), the scheduler loop itself is wedged — no matter how sparse
   the schedule. Critical.

2. ``cron_tick_fired`` — bookkeeping vs reality. A registered+enabled cron
   whose tick passed must leave a ``task_hub_runs`` row (the six-rule
   observability protocol's run history). Seeded ONLY with the crons that
   actually write run rows — asserting this for every cron would itself be a
   crying-wolf alarm, since most in-app system jobs are migrated to systemd
   timers (enabled=false here) and several enabled ones write no run rows.
   Deviation from the handoff's seed list for exactly that reason: of its
   four seeds, morning-briefing / evening-briefing / proactive-report-morning
   are systemd-owned now; the live run-row writers are
   ``paper_to_podcast_daily`` and ``morning_ideation_report``. Critical.

Both walk the same ``cron_jobs`` watchdog context ``cron_staleness`` uses
and run inside ``GET /api/v1/ops/proactive_health`` via the invariant
registry (registered on package import).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any, Dict, List, Optional

try:
    from croniter import croniter
except ImportError:  # croniter is in pyproject deps; paranoia branch
    croniter = None  # type: ignore[assignment]

from universal_agent.services.invariants.cron_staleness import (
    _expected_interval_seconds,
    _parse_timestamp,
    _resolve_tz,
)
from universal_agent.services.pipeline_invariants import invariant

logger = logging.getLogger(__name__)


# ── cron_loop_liveness thresholds ───────────────────────────────────────────
# Grace on top of next_run_at before an enabled cron counts as overdue:
#   threshold = clamp(interval × MULTIPLIER, FLOOR, CAP)
# every-minute (60s)  → 3 min   (floor)
# */15 (900s)         → ~22 min
# daily/weekly        → 2 h     (cap — the whole point: not 48h)
_OVERDUE_MULTIPLIER = 1.5
_OVERDUE_FLOOR_S = float(os.getenv("UA_CRON_LOOP_OVERDUE_FLOOR_S", "180"))
_OVERDUE_CAP_S = float(os.getenv("UA_CRON_LOOP_OVERDUE_CAP_S", "7200"))

# ── cron_tick_fired config ──────────────────────────────────────────────────
# Only crons that PROVABLY write task_hub_runs rows (task_id = 'cron:<key>').
_DEFAULT_TICK_SEEDS = "paper_to_podcast_daily,morning_ideation_report"
# How long after the scheduled tick a run row must have appeared.
_TICK_GRACE_MINUTES = float(os.getenv("UA_CRON_TICK_FIRED_GRACE_MINUTES", "30"))
# Slack before the tick: a run dispatched marginally early still counts.
_TICK_EARLY_SLACK_MINUTES = 5.0


def _tick_seed_jobs() -> list[str]:
    raw = os.getenv("UA_CRON_TICK_FIRED_JOBS", _DEFAULT_TICK_SEEDS)
    return [s.strip() for s in raw.split(",") if s.strip()]


def _job_dicts(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize ctx['cron_jobs'] rows to dicts (mirrors cron_staleness)."""
    out: List[Dict[str, Any]] = []
    for raw in ctx.get("cron_jobs") or ():
        if hasattr(raw, "to_dict"):
            try:
                out.append(raw.to_dict())
            except Exception:  # noqa: BLE001
                continue
        elif isinstance(raw, dict):
            out.append(raw)
    return out


def _system_job_key(job: Dict[str, Any]) -> str:
    meta = job.get("metadata")
    if isinstance(meta, dict):
        return str(meta.get("system_job") or "").strip()
    return ""


@invariant(
    id="cron_loop_liveness",
    title="Cron scheduler is dispatching: no enabled cron is past next_run_at",
    description=(
        "Every enabled in-app cron carries next_run_at — the scheduler's own "
        "promise of when it will fire. If now is past that promise by more "
        "than a cadence-scaled grace (3 min floor for every-minute crons, 2h "
        "cap for daily/weekly), the scheduler LOOP is wedged — a class "
        "cron_staleness only catches after 2× the schedule's longest gap "
        "(~48h for a daily cron; a wedge once went ~20h undetected)."
    ),
    severity="critical",
    runbook_command=(
        "python3 -c \"import json,time; jobs=json.load(open('/opt/universal_agent/"
        "AGENT_RUN_WORKSPACES/cron_jobs.json'))['jobs']; now=time.time(); "
        "[print(j['job_id'], j.get('metadata',{}).get('system_job'), "
        "round((now-float(j['next_run_at']))/60,1), 'min overdue') "
        "for j in jobs if j.get('enabled') and j.get('next_run_at') and "
        "float(j['next_run_at']) < now]\"; "
        "systemctl status universal-agent-gateway --no-pager | head -5"
    ),
    metadata={
        "pipeline": "cron_scheduler",
        "context_key": "cron_jobs",
        "overdue_multiplier": _OVERDUE_MULTIPLIER,
        "overdue_floor_s": _OVERDUE_FLOOR_S,
        "overdue_cap_s": _OVERDUE_CAP_S,
    },
)
def cron_loop_liveness(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Flag enabled crons whose next_run_at is in the past beyond the grace.

    Walks the ``cron_jobs`` watchdog context; for each enabled job with a
    parseable ``next_run_at``, computes overdue = now − next_run_at and flags
    when it exceeds clamp(interval × 1.5, 3 min, 2h). One finding lists every
    overdue cron. Returns None when the context is absent or all healthy.
    """
    jobs = _job_dicts(ctx)
    if not jobs:
        return None

    now = datetime.now(timezone.utc)
    overdue_jobs: List[Dict[str, Any]] = []
    for job in jobs:
        if not bool(job.get("enabled", True)):
            continue
        next_run = _parse_timestamp(job.get("next_run_at"))
        if next_run is None:
            continue
        overdue_s = (now - next_run).total_seconds()
        if overdue_s <= 0:
            continue
        cron_expr = str(job.get("cron_expr") or "").strip()
        tz = _resolve_tz(job.get("timezone"))
        interval = (
            _expected_interval_seconds(cron_expr, next_run, tz) if cron_expr else None
        )
        if interval is None:
            # every_seconds-style or unparseable schedule — use the raw value
            # when present, else be generous (1h → threshold capped at 2h).
            try:
                interval = float(job.get("every_seconds") or 3600.0)
            except (TypeError, ValueError):
                interval = 3600.0
        threshold = min(max(interval * _OVERDUE_MULTIPLIER, _OVERDUE_FLOOR_S), _OVERDUE_CAP_S)
        if overdue_s > threshold:
            overdue_jobs.append(
                {
                    "job_id": str(job.get("job_id") or "?"),
                    "system_job": _system_job_key(job) or None,
                    "cron_expr": cron_expr or None,
                    "next_run_at_utc": next_run.isoformat(),
                    "overdue_minutes": round(overdue_s / 60.0, 1),
                    "threshold_minutes": round(threshold / 60.0, 1),
                }
            )

    if not overdue_jobs:
        return None

    names = ", ".join(
        str(j["system_job"] or j["job_id"]) for j in overdue_jobs
    )
    worst = max(j["overdue_minutes"] for j in overdue_jobs)
    return {
        "observed_value": {
            "overdue_jobs": overdue_jobs,
            "overdue_count": len(overdue_jobs),
            "evaluated_at_utc": now.isoformat(),
        },
        "threshold_text": (
            f"every enabled cron fires within clamp(interval × {_OVERDUE_MULTIPLIER}, "
            f"{_OVERDUE_FLOOR_S / 60:.0f} min, {_OVERDUE_CAP_S / 3600:.0f}h) of next_run_at"
        ),
        "message": (
            f"{len(overdue_jobs)} enabled cron(s) past next_run_at beyond grace "
            f"(worst {worst:.0f} min): {names}. The scheduler loop is likely "
            "wedged — the jobs' own timeouts never arm if dispatch never "
            "starts. Check the gateway service and the cron dispatch loop."
        ),
    }


@invariant(
    id="cron_tick_fired",
    title="Run-row-writing crons left a task_hub_runs row for their last tick",
    description=(
        "For each seeded cron (only those that provably write task_hub_runs "
        "rows: task_id = 'cron:<system_job>'), assert a run row exists for "
        "the most recent scheduled tick once the grace window "
        f"({_TICK_GRACE_MINUTES:.0f} min) has passed. Catches "
        "bookkeeping-vs-reality divergence: registry says scheduled, but no "
        "run actually opened. Seeds are env-tunable "
        "(UA_CRON_TICK_FIRED_JOBS); disabled/absent seeds are skipped, not "
        "alarmed (operator choice is not an outage)."
    ),
    severity="critical",
    runbook_command=(
        "sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db "
        "\"SELECT task_id, started_at, outcome FROM task_hub_runs "
        "WHERE task_id LIKE 'cron:%' ORDER BY started_at DESC LIMIT 10;\""
    ),
    metadata={
        "pipeline": "cron_scheduler",
        "context_keys": ["cron_jobs", "activity_conn"],
        "tables": ["task_hub_runs"],
        "grace_minutes": _TICK_GRACE_MINUTES,
    },
)
def cron_tick_fired(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Flag seeded crons whose last scheduled tick produced no run row.

    For each seed in ``UA_CRON_TICK_FIRED_JOBS``: locate the enabled in-app
    job by ``metadata.system_job``, compute its most recent scheduled tick
    (croniter, in the job's declared timezone), and — once the tick is older
    than the grace window — require a ``task_hub_runs`` row for
    ``cron:<system_job>`` started at/after (tick − small slack). Returns None
    when croniter/conn/context are unavailable (fail-open) or all healthy.
    """
    conn = ctx.get("activity_conn")
    if conn is None or croniter is None:
        return None
    jobs = _job_dicts(ctx)
    if not jobs:
        return None
    by_key = {_system_job_key(j): j for j in jobs if _system_job_key(j)}

    now = datetime.now(timezone.utc)
    grace = timedelta(minutes=_TICK_GRACE_MINUTES)
    slack = timedelta(minutes=_TICK_EARLY_SLACK_MINUTES)
    missing: List[Dict[str, Any]] = []

    for seed in _tick_seed_jobs():
        job = by_key.get(seed)
        if job is None or not bool(job.get("enabled", True)):
            continue  # absent or operator-disabled — not an outage
        cron_expr = str(job.get("cron_expr") or "").strip()
        if not cron_expr:
            continue
        tz = _resolve_tz(job.get("timezone"))
        try:
            base = now.astimezone(tz) if tz is not None else now
            tick = croniter(cron_expr, base).get_prev(datetime)
            if tick.tzinfo is None:
                tick = tick.replace(tzinfo=timezone.utc)
            tick_utc = tick.astimezone(timezone.utc)
        except Exception:  # noqa: BLE001 — unparseable expr: skip, don't crash
            logger.debug("cron_tick_fired: cannot compute tick for %s", seed)
            continue
        if now - tick_utc < grace:
            continue  # too soon to judge this tick
        try:
            row = conn.execute(
                "SELECT MAX(started_at) AS latest FROM task_hub_runs WHERE task_id = ?",
                (f"cron:{seed}",),
            ).fetchone()
        except Exception:  # noqa: BLE001 — table absent on fresh box: fail-open
            logger.debug("cron_tick_fired: task_hub_runs query failed", exc_info=True)
            return None
        latest = _parse_timestamp(row["latest"] if row else None)
        if latest is not None and latest >= tick_utc - slack:
            continue
        missing.append(
            {
                "system_job": seed,
                "job_id": str(job.get("job_id") or "?"),
                "cron_expr": cron_expr,
                "tick_utc": tick_utc.isoformat(),
                "minutes_since_tick": round((now - tick_utc).total_seconds() / 60.0, 1),
                "latest_run_started_at": latest.isoformat() if latest else None,
            }
        )

    if not missing:
        return None

    names = ", ".join(m["system_job"] for m in missing)
    return {
        "observed_value": {
            "missing_ticks": missing,
            "missing_count": len(missing),
            "evaluated_at_utc": now.isoformat(),
        },
        "threshold_text": (
            f"each seeded cron opens a task_hub_runs row within "
            f"{_TICK_GRACE_MINUTES:.0f} min of its scheduled tick"
        ),
        "message": (
            f"{len(missing)} registered+enabled cron(s) produced NO "
            f"task_hub_runs row for their most recent scheduled tick: {names}. "
            "The registry scheduled the tick but no run opened — scheduler "
            "wedge or dispatch failure upstream of the job's own error "
            "handling. Check gateway logs around the tick time."
        ),
    }
