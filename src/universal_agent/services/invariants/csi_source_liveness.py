"""Universal CSI source liveness invariant.

One probe that watches every active CSI adapter (youtube_channel_rss,
threads_owned, threads_trends_seeded, threads_trends_broad, hackernews) by
checking `max(occurred_at)` per source in csi.db's `events` table against a
per-source expected-max-silence threshold.

`youtube_playlist` was dropped from monitoring 2026-06-03: the
youtube_playlist_watcher was retired in PR #438 (daily digest is the
canonical trigger), so it is intentionally silent and must not alert.

`csi_analytics` was dropped from monitoring 2026-06-15: the three trend-report
timers (csi-rss-trend-report, csi-global-trend-brief, csi-threads-trend-report)
that emitted `csi_analytics` events were retired in PR #990 (their output
duplicated the convergence pipeline), so the source is intentionally dormant
and must not alert. The only other scripts that can emit `csi_analytics`
(csi_validate_live_flow.py, csi_nightly_validation.py) are manual smoke/nightly
runners with no scheduled timer, so no active producer remains.

Why one invariant instead of six: the framework emits at most one finding
per invariant. Splitting into six would create six emails per dead CSI
day (spam) and six separate Task Hub rows to triage. A single finding
that lists every stale source gives the operator the full picture in one
alert, with the per-source breakdown surfaced in `observed_value`.

Was added 2026-05-20 (P1a) after a holistic audit found three adapters
silently dead for 40+ hours with zero watchdog coverage. Before this
probe, only `youtube_channel_rss` had any liveness coverage; the other
five adapters were invisible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Optional

from universal_agent.services.pipeline_invariants import invariant

logger = logging.getLogger(__name__)


# Threads CSI lanes are EXPERIMENTAL and parked by default (2026-06-03). The
# adapters skip every cycle when THREADS_USER_ID / THREADS_ACCESS_TOKEN are
# unset, so monitoring them produces a perpetual stale/never_seen alert that
# adds noise and can mask real outages in other lanes. They stay in
# SOURCE_THRESHOLDS_HOURS (so re-enabling is a one-flag flip) but are excluded
# from evaluation while parked. Set UA_CSI_THREADS_LANES_ENABLED=1 AND provision
# the Threads credentials to re-activate monitoring.
_THREADS_SOURCES = frozenset(
    {"threads_owned", "threads_trends_seeded", "threads_trends_broad"}
)


def _threads_lanes_enabled() -> bool:
    return os.getenv("UA_CSI_THREADS_LANES_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def effective_source_thresholds() -> Dict[str, float]:
    """The set of sources actually evaluated, after applying parking flags.

    Parked lanes are excluded so they neither alert nor mask other adapters'
    real outages:
      - Threads lanes while UA_CSI_THREADS_LANES_ENABLED is off (experimental,
        creds unprovisioned).
    A retired adapter (e.g. youtube_playlist #438, csi_analytics #990) is removed
    from the table outright; a *disabled-but-resumable* source is parked behind its flag here.

    hackernews is intentionally NOT parked. PR #757 (M3) parked it behind
    UA_HACKERNEWS_SNAPSHOT_ENABLED on the premise "snapshot cron disabled ⟹ HN
    source dead." Live data on 2026-06-06 disproved that: the hackernews_snapshot
    cron is disabled (#734) yet HN still lands ~130 events/48h — the overnight
    convergence cycle pulls HN via POST /api/v1/hackernews/refresh, a producer
    independent of the cron. So the snapshot-cron flag was the wrong key for HN
    liveness, and parking on it MASKED a live (bursty) source. HN is now monitored
    unconditionally at a cadence-appropriate threshold (see SOURCE_THRESHOLDS_HOURS).
    """
    threads_on = _threads_lanes_enabled()
    out: Dict[str, float] = {}
    for source, hours in SOURCE_THRESHOLDS_HOURS.items():
        if source in _THREADS_SOURCES and not threads_on:
            continue
        out[source] = hours
    return out


# Per-source expected max silence in hours. Conservative defaults: leave
# breathing room for low-cadence sources (threads_trends_*) while still
# catching the 40h+ failure mode that prompted P1a. Tune via follow-up PR
# once we have a few weeks of operational data — never silence an ACTIVE
# source entirely (retired adapters should be removed from this table, not
# given an unreachable threshold).
SOURCE_THRESHOLDS_HOURS: Dict[str, float] = {
    "hackernews": 120.0,                 # bursty: overnight convergence /refresh pulls (the */30 snapshot cron is disabled, #734). 30d live data (2026-06-10): a legitimate-but-quiet 94h gap occurred (06-06→06-09) while HN stayed alive; the next-largest gap was 27.6h. The old 36h false-flagged that 94h bursty spell (driving the proactive_health digest to flap critical/clear). 120h clears the observed 94h max with margin while still catching a real ≥5-day HN outage.
    "youtube_channel_rss": 12.0,         # 444-channel watchlist, hourly-ish per channel
    "threads_owned": 12.0,               # owned-handle polling
    "threads_trends_seeded": 24.0,       # broad seeded queries, lower cadence
    "threads_trends_broad": 24.0,        # broadest sweep, lower cadence
}


def _per_source_last_seen(
    csi_db_path: Path,
) -> Dict[str, Optional[datetime]]:
    """Return {source: max(occurred_at)} for every source listed in the
    threshold table. Sources that never appear in `events` get None.

    Bounded query: only look at the last 30 days. A source dead for 30+ days
    is treated as never_seen (operator should investigate the watchlist
    config or take it out of monitoring instead of letting it pile up alerts).
    """
    results: Dict[str, Optional[datetime]] = {s: None for s in effective_source_thresholds()}
    conn = sqlite3.connect(str(csi_db_path))
    try:
        cursor = conn.execute(
            "SELECT source, MAX(occurred_at) FROM events "
            "WHERE occurred_at >= datetime('now', '-30 days') "
            "GROUP BY source"
        )
        for source, last_seen_iso in cursor.fetchall():
            if source not in results:
                continue
            if last_seen_iso is None:
                continue
            try:
                # tolerate Z suffix and naive timestamps
                parsed = datetime.fromisoformat(str(last_seen_iso).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                results[source] = parsed
            except (ValueError, AttributeError):
                continue
    finally:
        conn.close()
    return results


@invariant(
    id="csi_source_liveness",
    title="CSI adapters are producing events on schedule",
    description=(
        "Watches max(occurred_at) per CSI source against a per-source "
        "expected-max-silence threshold. Fires when one or more adapters "
        "have gone quieter than their configured threshold. Replaces the "
        "previous coverage gap where only youtube_channel_rss was watched."
    ),
    severity="critical",
    runbook_command=(
        "sqlite3 /var/lib/universal-agent/csi/csi.db "
        "\"SELECT source, COUNT(*), MAX(occurred_at) FROM events "
        "WHERE occurred_at >= datetime('now','-7 days') GROUP BY source ORDER BY MAX(occurred_at);\"; "
        "sudo journalctl -u csi-ingester --since '6 hours ago' --no-pager | grep -iE 'error|fail'"
    ),
    metadata={
        "pipeline": "csi_ingester_adapters",
        "tables": ["events"],
        "db": "csi.db",
        "sources_monitored": list(SOURCE_THRESHOLDS_HOURS.keys()),
        "threads_lanes_flag": "UA_CSI_THREADS_LANES_ENABLED",
        "design_note": (
            "P1a (2026-05-20): one invariant covering the CSI adapters in one "
            "finding. Listing per-source thresholds + last_seen in "
            "observed_value lets the operator triage all stale sources from "
            "a single alert. Splitting per-source would spam the email/Task "
            "Hub channels. 2026-06-03: youtube_playlist removed (retired #438); "
            "Threads lanes (threads_owned, threads_trends_seeded, "
            "threads_trends_broad) parked behind UA_CSI_THREADS_LANES_ENABLED "
            "(experimental, creds unprovisioned) so they don't alert while off. "
            "2026-06-06: hackernews un-parked + threshold widened 3h->36h — the "
            "#757 park keyed on the (disabled) snapshot cron, but HN stays alive "
            "via the overnight convergence /refresh, so the park had masked a "
            "live bursty source. 2026-06-10: widened 36h->120h — 30d live data "
            "showed a legitimate 94h bursty gap (max; next-largest 27.6h) that "
            "36h false-flagged, which also drove the proactive_health digest to "
            "re-spam on every critical/clear flip. 2026-06-15: csi_analytics removed (retired #990 — the three trend-report timers that emitted it were retired; no active producer remains, the validation scripts that can still emit it are unscheduled manual runners)."
        ),
    },
)
def csi_source_liveness(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Flag CSI adapters that have gone quieter than their silence threshold.

    Reads ``MAX(occurred_at)`` per source from the CSI ``events`` table and
    compares each against its per-source expected-max-silence threshold (see
    ``effective_source_thresholds``). Stale sources are listed together in one
    critical finding so the operator can triage them from a single alert.
    Fails open (returns None) when the CSI DB is absent.
    """
    csi_db_path = ctx.get("csi_db_path")
    if csi_db_path is None:
        return None
    path = Path(str(csi_db_path))
    if not path.exists():
        return None
    try:
        last_seen = _per_source_last_seen(path)
    except sqlite3.OperationalError as exc:
        # No events table yet (early dev / fresh DB) — silent.
        logger.debug("csi_source_liveness: events table unavailable (%s)", exc)
        return None
    except sqlite3.Error as exc:
        logger.warning("csi_source_liveness: query failed: %s", exc, exc_info=True)
        return None

    now = datetime.now(timezone.utc)
    stale_sources: list[dict[str, Any]] = []
    for source, threshold_h in effective_source_thresholds().items():
        ts = last_seen.get(source)
        if ts is None:
            stale_sources.append(
                {
                    "source": source,
                    "threshold_hours": threshold_h,
                    "silence_hours": None,
                    "state": "never_seen",
                    "last_event_utc": None,
                }
            )
            continue
        silence = (now - ts).total_seconds() / 3600.0
        if silence > threshold_h:
            stale_sources.append(
                {
                    "source": source,
                    "threshold_hours": threshold_h,
                    "silence_hours": round(silence, 1),
                    "state": "stale",
                    "last_event_utc": ts.isoformat(),
                }
            )

    if not stale_sources:
        return None

    source_names = sorted(s["source"] for s in stale_sources)
    return {
        "observed_value": {
            "stale_sources": stale_sources,
            "stale_count": len(stale_sources),
            "monitored_count": len(effective_source_thresholds()),
            "evaluated_at_utc": now.isoformat(),
        },
        "threshold_text": (
            "every monitored CSI source produces an event within its "
            "per-source threshold (see metadata.sources_monitored)"
        ),
        "message": (
            f"{len(stale_sources)} CSI adapter(s) past their expected-silence "
            f"threshold: {', '.join(source_names)}. The csi-ingester service "
            f"is up but these adapters are not landing events. Investigate "
            f"the adapter loop, polling errors, or upstream rate limits."
        ),
    }
