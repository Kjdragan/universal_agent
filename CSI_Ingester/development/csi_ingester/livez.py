"""Pure, schedule-aware liveness decision for the `/livez` endpoint.

Background (T13): `/healthz` is a static, unconditional stub — it always
returns 200 as long as the uvicorn event loop can answer a request. The VPS
watchdog (`scripts/vps_service_watchdog.sh`) uses it as the CSI ingester's
*only* restart trigger, which means a poller wedged inside an otherwise-live
process is invisible and un-restartable.

`/livez` closes that gap for the `youtube_channel_rss` adapter — the one
adapter with hour-of-day schedule gating (`schedule_fetch_hours`,
`csi_ingester.adapters.youtube_channel_rss.YouTubeChannelRSSAdapter`). It must
NOT fire during a deliberate quiet window (RSS staleness there is by design —
this was the T13 incident's false-positive), and must fire only when a fetch
is genuinely overdue given the configured schedule.

This module contains no I/O and no FastAPI/adapter imports so it can be unit
tested directly without spinning up the app or the DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9 fallback, unused in this repo (py3.12+)
    ZoneInfo = None  # type: ignore[assignment]

# 2x the largest expected gap between scheduled fetch windows. Generous by
# design — this is a "the poller is truly wedged" signal, not a freshness SLO
# (that job belongs to the transcript canary / source-liveness invariants).
DEFAULT_GRACE_MULTIPLIER = 2.0
# Floor so a tightly-packed schedule (e.g. every hour) can't produce an
# absurdly small grace window that flaps on ordinary jitter.
DEFAULT_MIN_GRACE_SECONDS = 1800.0  # 30 minutes
DEFAULT_TIMEZONE = "America/Chicago"


@dataclass(frozen=True)
class LivezStatus:
    """Result of a single liveness evaluation."""

    healthy: bool
    reason: str
    in_quiet_window: bool
    seconds_since_fetch: float | None
    grace_seconds: float


def compute_largest_gap_hours(schedule_fetch_hours: list[int]) -> float:
    """Largest gap, in hours, between consecutive scheduled fetch hours.

    Hours wrap around midnight (e.g. schedule [2, 20] has a 6h gap 20->02,
    not an 18h one). Returns 24.0 when there is no meaningful gate: an empty
    schedule (fetches aren't hour-gated at all) or a single configured hour
    (the "gap" is the whole day until it recurs).
    """
    if not schedule_fetch_hours:
        return 24.0
    hours = sorted({int(h) % 24 for h in schedule_fetch_hours})
    if len(hours) <= 1:
        return 24.0
    gaps: list[int] = []
    for i, hour in enumerate(hours):
        nxt = hours[(i + 1) % len(hours)]
        gap = (nxt - hour) % 24
        gaps.append(gap if gap else 24)
    return float(max(gaps))


def compute_grace_seconds(
    *,
    schedule_fetch_hours: list[int],
    poll_interval_seconds: float = 60.0,
    grace_multiplier: float = DEFAULT_GRACE_MULTIPLIER,
    min_grace_seconds: float = DEFAULT_MIN_GRACE_SECONDS,
) -> float:
    """A generous "fetch is overdue" threshold derived from the schedule.

    Default: `grace_multiplier` x the largest expected inter-fetch gap. When
    no hour-of-day gating is configured, the adapter fetches on every poll
    tick, so the "gap" is simply `poll_interval_seconds`.
    """
    if schedule_fetch_hours:
        largest_gap_seconds = compute_largest_gap_hours(schedule_fetch_hours) * 3600.0
    else:
        largest_gap_seconds = max(60.0, float(poll_interval_seconds))
    grace = largest_gap_seconds * max(1.0, float(grace_multiplier))
    return max(grace, float(min_grace_seconds))


def is_quiet_window(*, schedule_fetch_hours: list[int], now: datetime) -> bool:
    """True when the schedule deliberately excludes the current hour from fetching."""
    if not schedule_fetch_hours:
        return False
    return now.hour not in {int(h) % 24 for h in schedule_fetch_hours}


def _resolve_timezone(name: str):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name or DEFAULT_TIMEZONE)
        except Exception:
            pass
    return timezone.utc


def compute_livez_status(
    *,
    schedule_fetch_hours: list[int],
    schedule_timezone: str = DEFAULT_TIMEZONE,
    poll_interval_seconds: float = 60.0,
    last_fetch_epoch: float,
    now_epoch: float,
    grace_multiplier: float = DEFAULT_GRACE_MULTIPLIER,
    min_grace_seconds: float = DEFAULT_MIN_GRACE_SECONDS,
) -> LivezStatus:
    """Decide whether the RSS poller is alive, schedule-aware.

    Healthy when EITHER:
      - the current hour is outside the configured fetch-hour schedule (a
        deliberate quiet window — RSS staleness there is expected), OR
      - a fetch completed within `grace_seconds` of `now_epoch`.

    Unhealthy only when a fetch is genuinely overdue given the schedule
    (outside any quiet window, and past the grace threshold).
    """
    grace_seconds = compute_grace_seconds(
        schedule_fetch_hours=schedule_fetch_hours,
        poll_interval_seconds=poll_interval_seconds,
        grace_multiplier=grace_multiplier,
        min_grace_seconds=min_grace_seconds,
    )
    tz = _resolve_timezone(schedule_timezone)
    now_dt = datetime.fromtimestamp(now_epoch, tz=tz)
    quiet = is_quiet_window(schedule_fetch_hours=schedule_fetch_hours, now=now_dt)

    if last_fetch_epoch <= 0:
        # No fetch observed yet (fresh deploy, or state not yet persisted).
        # There's no baseline to measure "overdue" against, and the
        # scheduler runs its first tick immediately on startup — a healthy
        # process clears this within seconds. Insufficient evidence to
        # declare unhealthy.
        return LivezStatus(
            healthy=True,
            reason="no_fetch_observed_yet",
            in_quiet_window=quiet,
            seconds_since_fetch=None,
            grace_seconds=grace_seconds,
        )

    seconds_since_fetch = max(0.0, now_epoch - last_fetch_epoch)

    if quiet:
        return LivezStatus(
            healthy=True,
            reason="quiet_window",
            in_quiet_window=True,
            seconds_since_fetch=seconds_since_fetch,
            grace_seconds=grace_seconds,
        )

    if seconds_since_fetch < grace_seconds:
        return LivezStatus(
            healthy=True,
            reason="within_grace",
            in_quiet_window=False,
            seconds_since_fetch=seconds_since_fetch,
            grace_seconds=grace_seconds,
        )

    return LivezStatus(
        healthy=False,
        reason="fetch_overdue",
        in_quiet_window=False,
        seconds_since_fetch=seconds_since_fetch,
        grace_seconds=grace_seconds,
    )
