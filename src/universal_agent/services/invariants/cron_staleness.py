"""Universal cron last-run staleness invariant.

After P0a (PR #395) the watchdog sidecar's `crons[]` is populated with
every persisted cron job (job_id, enabled, cron_expr, last_run_at,
last_outcome, next_run_at). P1b adds the matching Layer-2 invariant:
walk every enabled cron, derive its expected interval from the cron_expr,
and fire when last_run is >2× past that interval.

Three failure modes flagged:
1. `stale` — last_run > 2× expected interval old.
2. `last_outcome_error` — recent run, but the last outcome wasn't success.
3. `never_run` — last_run is None AND next_run was in the past
   (registered but never fired). Brand-new crons with future next_run
   stay quiet.

One invariant, one finding listing every stale cron. Splitting per-cron
would emit up to 22 alerts on a bad day.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional

try:
    from croniter import croniter
except ImportError:  # croniter is in pyproject deps; this is a paranoia branch
    croniter = None  # type: ignore[assignment]

from universal_agent.services.pipeline_invariants import invariant

logger = logging.getLogger(__name__)


# Multiplier on the cron's expected interval. last_run > MULTIPLIER × interval
# old is flagged. 2.0 gives one cycle of grace so a cron that simply ran a
# little late doesn't trip the alarm.
STALENESS_MULTIPLIER = 2.0

# Floor: any cron whose interval is less than this gets this as its threshold
# instead. Prevents `*/1 * * * *` crons from firing on 2-min lag.
MIN_STALENESS_SECONDS = 300.0  # 5 min


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Accept epoch float, epoch int, or ISO string; return aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, AttributeError):
        return None


def _expected_interval_seconds(cron_expr: str, ref_dt: datetime) -> Optional[float]:
    """Compute the gap between the next two scheduled fires after `ref_dt`.

    This is the cron's expected cadence as advertised by its expression.
    Returns None if the expression is unparseable.
    """
    if croniter is None:
        return None
    try:
        c = croniter(cron_expr, ref_dt)
        first = c.get_next(datetime)
        second = c.get_next(datetime)
        # Ensure aware datetimes for subtraction.
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        if second.tzinfo is None:
            second = second.replace(tzinfo=timezone.utc)
        return (second - first).total_seconds()
    except Exception:  # noqa: BLE001 — croniter raises a variety of types
        return None


def _evaluate_cron(cron: Dict[str, Any], now: datetime) -> Optional[Dict[str, Any]]:
    """Return a {job_id, reason, ...} dict if the cron is in trouble; None if OK.

    Caller filters disabled crons before calling.
    """
    job_id = str(cron.get("job_id") or "?")
    cron_expr = str(cron.get("cron_expr") or "").strip()
    if not cron_expr:
        return None

    last_run = _parse_timestamp(cron.get("last_run_at"))
    next_run = _parse_timestamp(cron.get("next_run_at"))
    last_outcome = str(cron.get("last_outcome") or "").strip().lower()

    interval = _expected_interval_seconds(cron_expr, last_run or now)
    if interval is None:
        # Unparseable cron_expr — skip silently, don't crash.
        logger.debug("cron_staleness: cannot parse cron_expr for %s: %r", job_id, cron_expr)
        return None
    threshold_seconds = max(MIN_STALENESS_SECONDS, interval * STALENESS_MULTIPLIER)

    # Case 1: never run
    if last_run is None:
        if next_run is not None and next_run < now:
            return {
                "job_id": job_id,
                "reason": "never_run",
                "cron_expr": cron_expr,
                "expected_interval_seconds": round(interval, 1),
                "last_run_at": None,
                "next_run_at_utc": next_run.isoformat(),
            }
        return None

    # Case 2: stale by timing
    age_seconds = (now - last_run).total_seconds()
    if age_seconds > threshold_seconds:
        return {
            "job_id": job_id,
            "reason": "stale",
            "cron_expr": cron_expr,
            "expected_interval_seconds": round(interval, 1),
            "threshold_seconds": round(threshold_seconds, 1),
            "age_seconds": round(age_seconds, 1),
            "last_run_at_utc": last_run.isoformat(),
            "last_outcome": last_outcome or None,
        }

    # Case 3: ran on time but outcome was an error
    if last_outcome and last_outcome not in {"success", "ok", "clean_exit_zero", "completed"}:
        return {
            "job_id": job_id,
            "reason": "last_outcome_error",
            "cron_expr": cron_expr,
            "last_run_at_utc": last_run.isoformat(),
            "last_outcome": last_outcome,
        }

    return None


@invariant(
    id="cron_staleness",
    title="Every enabled cron is firing on schedule with successful outcomes",
    description=(
        "Walks `cron_jobs` from the watchdog context (sourced from "
        "CronStore persistence file or in-memory CronService). For each "
        "enabled cron: computes the expected interval from its cron_expr, "
        "compares last_run_at against the threshold, and inspects "
        "last_outcome. Emits one finding listing every cron in trouble."
    ),
    severity="warn",
    runbook_command=(
        "ls -la /opt/universal_agent/AGENT_RUN_WORKSPACES/cron_jobs.json; "
        "tail -50 /opt/universal_agent/AGENT_RUN_WORKSPACES/cron_runs.jsonl | python3 -c \""
        "import json,sys; [print(json.loads(l)) for l in sys.stdin]\""
    ),
    metadata={
        "context_key": "cron_jobs",
        "design_note": (
            "P1b (2026-05-20): one invariant for all crons. The multiplier "
            "of 2× the expected interval gives one cycle of grace. Floor "
            "of 5 min keeps every-minute crons from tripping on transient "
            "lag. Listing all stale crons in one finding avoids 22 alerts "
            "on a bad day."
        ),
        "staleness_multiplier": STALENESS_MULTIPLIER,
        "min_staleness_seconds": MIN_STALENESS_SECONDS,
    },
)
def cron_staleness(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Flag enabled crons that are overdue or whose last outcome failed.

    Walks the ``cron_jobs`` watchdog context; for each enabled cron it derives
    the expected interval from ``cron_expr``, compares ``last_run_at`` against
    the staleness threshold, and inspects ``last_outcome``. Every cron in
    trouble is collected into a single finding (one alert, not one per cron).
    Returns None when ``cron_jobs`` is absent or all crons are healthy.
    """
    cron_jobs = ctx.get("cron_jobs")
    if not cron_jobs:
        return None

    now = datetime.now(timezone.utc)
    stale_crons: list[Dict[str, Any]] = []

    for raw in cron_jobs:
        # Tolerate either dict-shaped rows (from CronStore.load_jobs after
        # to_dict, or from the gateway endpoint's _summarize_cron_jobs) or
        # objects exposing .to_dict.
        if hasattr(raw, "to_dict"):
            try:
                cron = raw.to_dict()
            except Exception:  # noqa: BLE001
                continue
        elif isinstance(raw, dict):
            cron = raw
        else:
            continue

        if not bool(cron.get("enabled", True)):
            continue

        try:
            result = _evaluate_cron(cron, now)
        except Exception as exc:  # noqa: BLE001 — defensive, never crash the runner
            logger.debug("cron_staleness: evaluate failed for %r: %s", cron.get("job_id"), exc)
            continue
        if result is not None:
            stale_crons.append(result)

    if not stale_crons:
        return None

    job_ids = sorted(s["job_id"] for s in stale_crons)
    return {
        "observed_value": {
            "stale_crons": stale_crons,
            "stale_count": len(stale_crons),
            "evaluated_at_utc": now.isoformat(),
        },
        "threshold_text": (
            f"every enabled cron's last_run within {STALENESS_MULTIPLIER}× its "
            "expected interval AND last_outcome is success"
        ),
        "message": (
            f"{len(stale_crons)} cron job(s) in trouble: {', '.join(job_ids)}. "
            f"Review last_outcome on each; check journalctl for the gateway "
            f"service and cron_runs.jsonl for the per-job failure context."
        ),
    }
