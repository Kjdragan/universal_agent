"""Universal CSI source liveness invariant.

One probe that watches every active CSI adapter (youtube_channel_rss,
reddit_discovery, threads_owned, threads_trends_seeded, threads_trends_broad,
hackernews, csi_analytics) by checking `max(occurred_at)` per source in
csi.db's `events` table against a per-source expected-max-silence threshold.

`youtube_playlist` was dropped from monitoring 2026-06-03: the
youtube_playlist_watcher was retired in PR #438 (daily digest is the
canonical trigger), so it is intentionally silent and must not alert.

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
from pathlib import Path
import sqlite3
from typing import Any, Dict, Optional

from universal_agent.services.pipeline_invariants import invariant

logger = logging.getLogger(__name__)


# Per-source expected max silence in hours. Conservative defaults: leave
# breathing room for low-cadence sources (threads_trends_*) while still
# catching the 40h+ failure mode that prompted P1a. Tune via follow-up PR
# once we have a few weeks of operational data — never silence an ACTIVE
# source entirely (retired adapters should be removed from this table, not
# given an unreachable threshold).
SOURCE_THRESHOLDS_HOURS: Dict[str, float] = {
    "hackernews": 3.0,                   # very high frequency (every 30 min cron + adapter poll)
    "csi_analytics": 12.0,               # downstream aggregator — depends on upstream cadence
    "youtube_channel_rss": 12.0,         # 444-channel watchlist, hourly-ish per channel
    "reddit_discovery": 12.0,            # subreddit polling
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
    results: Dict[str, Optional[datetime]] = {s: None for s in SOURCE_THRESHOLDS_HOURS}
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
        "design_note": (
            "P1a (2026-05-20): one invariant covering six adapters in one "
            "finding. Listing per-source thresholds + last_seen in "
            "observed_value lets the operator triage all stale sources from "
            "a single alert. Splitting per-source would spam the email/Task "
            "Hub channels."
        ),
    },
)
def csi_source_liveness(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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
    for source, threshold_h in SOURCE_THRESHOLDS_HOURS.items():
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
            "monitored_count": len(SOURCE_THRESHOLDS_HOURS),
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
