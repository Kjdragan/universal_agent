"""Universal cron last-run staleness invariant.

After P0a (PR #395) the watchdog sidecar's `crons[]` is populated with
every persisted cron job (job_id, enabled, cron_expr, timezone,
last_run_at, last_outcome, next_run_at). P1b adds the matching Layer-2
invariant: walk every enabled cron, derive its expected interval from the
cron_expr, and fire when last_run is >2× past that interval.

The interval is the MAX gap between consecutive fires across a full day,
evaluated in the cron's declared timezone — not the gap to the next two
fires. Asymmetric schedules (e.g. `35 10,15 * * *` America/Chicago, a 5h
leg and a ~19h overnight leg) would otherwise report the short leg as the
cadence and flag the long overnight quiet period as stale every night.

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

try:
    import pytz
except ImportError:  # pytz is in pyproject deps; this is a paranoia branch
    pytz = None  # type: ignore[assignment]

from universal_agent.services.pipeline_invariants import invariant

logger = logging.getLogger(__name__)


# Multiplier on the cron's expected interval. last_run > MULTIPLIER × interval
# old is flagged. 2.0 gives one cycle of grace so a cron that simply ran a
# little late doesn't trip the alarm.
STALENESS_MULTIPLIER = 2.0

# Floor: any cron whose interval is less than this gets this as its threshold
# instead. Prevents `*/1 * * * *` crons from firing on 2-min lag.
MIN_STALENESS_SECONDS = 300.0  # 5 min

# Window for sampling fires when deriving the expected interval. We collect
# fires until they span at least this long (so a full day — DST-safe — of an
# asymmetric schedule's gaps is observed) before taking the MAX gap. 26h
# covers the 25h fall-back DST day with margin.
_INTERVAL_WINDOW_SECONDS = 26 * 3600.0

# Pathological-loop guard on fires sampled per interval computation. The
# span-based stop above is the primary terminator; this cap only protects
# against a degenerate expression. It is sized so that even a minute-grained
# schedule (`*/1`, ~1560 fires to span 26h) still reaches the window — a lower
# cap would truncate a dense-but-windowed schedule (e.g. `*/5 9-17`, ~110
# fires to its overnight gap) BEFORE its long gap, reporting the short daytime
# leg as the cadence and re-introducing the false overnight stale. At minute
# granularity this is ~48ms worst-case per cron; every sparse cron stops in a
# handful of fires (<3ms).
_INTERVAL_MAX_FIRES = 1600


def _resolve_tz(timezone_name: Any) -> Any:
    """Return a tzinfo for the cron's declared timezone, or None for UTC.

    None means "treat the cron_expr as UTC" — croniter's default — which is
    correct both for crons that declare ``UTC`` and as a safe fallback when
    pytz is missing or the name is unknown.
    """
    name = str(timezone_name or "").strip()
    if not name or name.upper() == "UTC" or pytz is None:
        return None
    try:
        return pytz.timezone(name)
    except Exception:  # noqa: BLE001 — UnknownTimeZoneError and friends
        return None


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


def _expected_interval_seconds(
    cron_expr: str, ref_dt: datetime, tz: Any = None
) -> Optional[float]:
    """Return the cron's *longest* legitimate gap between consecutive fires.

    The previous implementation sampled only the first two fires after
    ``ref_dt`` and assumed a constant cadence. That is wrong for asymmetric
    schedules: ``35 10,15 * * *`` has a 5h leg and a ~19h overnight leg, so
    depending on where ``ref_dt`` landed it returned 5h, making the overnight
    quiet period look stale every night.

    Instead we sample a full day (DST-safe) of fires and return the MAX gap
    between consecutive fires. The staleness threshold (interval × multiplier)
    is therefore keyed to the longest the cron is *expected* to be quiet, so
    no leg of an asymmetric schedule trips a false alarm. Using the max gap
    (a property of the schedule alone) rather than the gap to the next fire
    also makes the check robust to a cron that simply ran late.

    ``tz`` (a tzinfo, e.g. ``pytz.timezone("America/Chicago")``) localizes the
    cron_expr so a schedule declared in local time is interpreted correctly.
    None evaluates the expression in UTC. Returns None if unparseable or if
    fewer than two fires could be sampled.
    """
    if croniter is None:
        return None
    try:
        base = ref_dt
        if tz is not None:
            try:
                base = ref_dt.astimezone(tz)
            except Exception:  # noqa: BLE001 — bad tzinfo; fall back to ref as-is
                base = ref_dt
        c = croniter(cron_expr, base)
        fires: list[datetime] = []
        while len(fires) < _INTERVAL_MAX_FIRES:
            nxt = c.get_next(datetime)
            if nxt.tzinfo is None:
                nxt = nxt.replace(tzinfo=timezone.utc)
            fires.append(nxt)
            if len(fires) >= 2 and (fires[-1] - base).total_seconds() > _INTERVAL_WINDOW_SECONDS:
                break
        if len(fires) < 2:
            return None
        gaps = [
            (fires[i + 1] - fires[i]).total_seconds()
            for i in range(len(fires) - 1)
        ]
        gaps = [g for g in gaps if g > 0]
        return max(gaps) if gaps else None
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

    # Honor the cron's declared timezone so a local-time expression (e.g.
    # "35 10,15 * * *" America/Chicago) is interpreted in the same tz the
    # scheduler uses, not blindly as UTC.
    tz = _resolve_tz(cron.get("timezone"))
    interval = _expected_interval_seconds(cron_expr, last_run or now, tz)
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
            "on a bad day. The expected interval is the MAX gap across a "
            "full day of fires, evaluated in the cron's declared timezone "
            "(2026-06-04 fix): asymmetric schedules like `35 10,15` "
            "America/Chicago no longer flag their long overnight leg as "
            "stale every night. Tradeoff: timing-staleness latency is keyed "
            "to the longest quiet leg (threshold = 2× the max gap), so a cron "
            "that dies during its active window is caught later than its "
            "active cadence would suggest — per-run failures are caught "
            "independently by last_outcome_error here and by the "
            "cron_consecutive_failures invariant."
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
