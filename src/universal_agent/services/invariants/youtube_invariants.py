"""YouTube pipeline invariants.

Motivation: the operator observed that 100% of YouTube cards over a 7-day
window had ``transcript_status='missing'``, even when ingest demonstrably
succeeded.  The dashboard reads events LEFT JOIN rss_event_analysis WHERE
source='youtube_channel_rss'; the LEFT JOIN produced NULLs that coalesce to
"missing" in the UI.  No process-level health check caught it because the
cron exited cleanly and the task row closed.

Two invariants live here, each covering a distinct failure mode the original
incident touched:

1. ``youtube_enrichment_coverage`` — coarse cross-table check that catches the
   exact original failure: events arrived (rows exist in ``events`` with
   source='youtube_channel_rss') but very few have a matching row in
   ``rss_event_analysis``.  Fires when the enrichment pipeline isn't running
   or is writing to the wrong table.

2. ``youtube_transcript_coverage`` — fine-grained check on rows that DID make
   it into ``rss_event_analysis``.  Fires when enrichment ran but most rows
   carry ``transcript_status != 'ok'``.

Both run every heartbeat; together they cover "enrichment never wrote" and
"enrichment wrote bad status."
"""

from __future__ import annotations

import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, Optional

from universal_agent.services.pipeline_invariants import invariant

logger = logging.getLogger(__name__)

# A day is "investigatable" only once we have at least this many rows; with
# tiny samples (e.g. weekends with one upload) a single missing transcript
# would trip the alert spuriously.
MIN_ROWS_PER_DAY = 3
# Below this transcript-OK percentage on a populated day, the pipeline is
# considered broken for that day.
OK_PCT_FLOOR = 50.0
WINDOW_DAYS = 7

# Enrichment-coverage thresholds.  Below COVERAGE_FLOOR_PCT of events having
# a matching rss_event_analysis row over the last 7 days, the pipeline is
# considered broken.  The MIN_EVENTS guard keeps small-sample days quiet.
COVERAGE_FLOOR_PCT = 50.0
COVERAGE_MIN_EVENTS = 5


def _resolve_csi_db_path(ctx: Dict[str, Any]) -> Optional[Path]:
    raw = ctx.get("csi_db_path")
    if raw is None:
        return None
    path = Path(raw) if not isinstance(raw, Path) else raw
    if not path.exists():
        return None
    return path


@invariant(
    id="youtube_transcript_coverage",
    title="YouTube transcript coverage over last 7 days",
    description=(
        "Every populated day in the last 7 days should have at least "
        f"{OK_PCT_FLOOR:.0f}% of rss_event_analysis rows for "
        "source='youtube_channel_rss' with transcript_status='ok'. A populated "
        f"day is one with >= {MIN_ROWS_PER_DAY} rows."
    ),
    severity="critical",
    runbook_command=(
        "sqlite3 \"$UA_CSI_DB_PATH\" \"SELECT DATE(analyzed_at) day, "
        "COUNT(*) total, SUM(CASE WHEN transcript_status='ok' THEN 1 ELSE 0 END) ok "
        "FROM rss_event_analysis WHERE source='youtube_channel_rss' AND "
        "analyzed_at >= datetime('now','-7 days') GROUP BY day ORDER BY day DESC;\""
    ),
    metadata={
        "pipeline": "youtube_daily_digest",
        "tables": ["rss_event_analysis"],
        "doc": "docs/03_Operations/132_Proactive_Health_Watchdog.md",
    },
)
def youtube_transcript_coverage(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Probe rss_event_analysis for transcript-status coverage."""
    csi_db_path = _resolve_csi_db_path(ctx)
    if csi_db_path is None:
        # CSI DB not available in this environment — invariant is N/A, not
        # broken.  The watchdog needs to fail OPEN so dev / fresh boxes
        # don't get a wall of warn findings before they have data.
        return None

    db = sqlite3.connect(f"file:{csi_db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            """
            SELECT DATE(analyzed_at) AS day,
                   COUNT(*) AS total,
                   SUM(CASE WHEN transcript_status = 'ok' THEN 1 ELSE 0 END) AS ok_count
            FROM rss_event_analysis
            WHERE source = 'youtube_channel_rss'
              AND analyzed_at >= datetime('now', ?)
            GROUP BY DATE(analyzed_at)
            ORDER BY day DESC
            """,
            (f"-{WINDOW_DAYS} days",),
        ).fetchall()
    except sqlite3.Error as exc:
        # Table missing or schema mismatch — surface as probe_error via
        # raising so the runner converts it.  Don't swallow.
        raise RuntimeError(f"rss_event_analysis query failed: {exc}") from exc
    finally:
        db.close()

    offending: list[Dict[str, Any]] = []
    days_inspected = 0
    for row in rows:
        total = int(row["total"] or 0)
        ok = int(row["ok_count"] or 0)
        if total < MIN_ROWS_PER_DAY:
            continue
        days_inspected += 1
        ok_pct = (100.0 * ok / total) if total else 0.0
        if ok_pct < OK_PCT_FLOOR:
            offending.append(
                {
                    "day": str(row["day"] or ""),
                    "total": total,
                    "ok_count": ok,
                    "ok_pct": round(ok_pct, 1),
                }
            )

    if not offending:
        return None

    return {
        "observed_value": {
            "offending_days": offending,
            "days_inspected": days_inspected,
            "window_days": WINDOW_DAYS,
        },
        "message": (
            f"{len(offending)} of {days_inspected} populated day(s) in the last "
            f"{WINDOW_DAYS} days had transcript ok_pct < {OK_PCT_FLOOR:.0f}%. "
            "The enrichment pipeline (csi_rss_semantic_enrich.py) is writing "
            "rows but most are failing to capture a transcript."
        ),
        "threshold_text": (
            f"ok_pct >= {OK_PCT_FLOOR:.0f}% on every day with >= "
            f"{MIN_ROWS_PER_DAY} rows"
        ),
        "metadata": {
            "min_rows_per_day": MIN_ROWS_PER_DAY,
            "ok_pct_floor": OK_PCT_FLOOR,
        },
    }


@invariant(
    id="youtube_enrichment_coverage",
    title="YouTube enrichment coverage over last 7 days",
    description=(
        "Of YouTube events received in the last 7 days, at least "
        f"{COVERAGE_FLOOR_PCT:.0f}% must have a matching rss_event_analysis "
        "row.  Catches the original failure mode where youtube_daily_digest "
        "succeeded but its output never reached the table the UI reads."
    ),
    severity="critical",
    runbook_command=(
        "sqlite3 \"$UA_CSI_DB_PATH\" \"SELECT COUNT(*) total_events, "
        "SUM(CASE WHEN a.event_id IS NOT NULL THEN 1 ELSE 0 END) enriched "
        "FROM events e LEFT JOIN rss_event_analysis a ON a.event_id = e.event_id "
        "WHERE e.source='youtube_channel_rss' AND e.occurred_at >= "
        "datetime('now','-7 days');\""
    ),
    metadata={
        "pipeline": "youtube_daily_digest + csi_rss_semantic_enrich",
        "tables": ["events", "rss_event_analysis"],
        "doc": "docs/03_Operations/132_Proactive_Health_Watchdog.md",
    },
)
def youtube_enrichment_coverage(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Probe for the empty-rss_event_analysis failure mode.

    Joins ``events`` (the ingest-side table) against ``rss_event_analysis``
    (the enrichment-side table).  When ingest succeeded but enrichment never
    wrote — the exact original 38/38 incident — events exist with no match.
    """
    csi_db_path = _resolve_csi_db_path(ctx)
    if csi_db_path is None:
        return None

    db = sqlite3.connect(f"file:{csi_db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute(
            """
            SELECT
                COUNT(*) AS total_events,
                SUM(CASE WHEN a.event_id IS NOT NULL THEN 1 ELSE 0 END) AS enriched_events
            FROM events e
            LEFT JOIN rss_event_analysis a ON a.event_id = e.event_id
            WHERE e.source = 'youtube_channel_rss'
              AND e.occurred_at >= datetime('now', ?)
            """,
            (f"-{WINDOW_DAYS} days",),
        ).fetchone()
    except sqlite3.Error as exc:
        # `events` table can legitimately be absent on a fresh dev box.  Treat
        # as N/A rather than surfacing a probe_error every heartbeat.
        logger.debug("youtube_enrichment_coverage: query failed (%s); skipping", exc)
        return None
    finally:
        db.close()

    total = int((row["total_events"] if row else 0) or 0)
    enriched = int((row["enriched_events"] if row else 0) or 0)

    if total < COVERAGE_MIN_EVENTS:
        # Too few events to make a useful claim.  Stay quiet.
        return None

    coverage_pct = (100.0 * enriched / total) if total else 0.0
    if coverage_pct >= COVERAGE_FLOOR_PCT:
        return None

    return {
        "observed_value": {
            "total_events": total,
            "enriched_events": enriched,
            "coverage_pct": round(coverage_pct, 1),
            "window_days": WINDOW_DAYS,
        },
        "message": (
            f"{enriched} of {total} YouTube events in the last "
            f"{WINDOW_DAYS} days have a matching rss_event_analysis row "
            f"(coverage {coverage_pct:.1f}% < floor {COVERAGE_FLOOR_PCT:.0f}%). "
            "Likely cause: ingest succeeded but enrichment never wrote, or "
            "wrote to a different table (e.g. csi_digests).  Check "
            "csi_rss_semantic_enrich.py is running and reading from events "
            "with source='youtube_channel_rss'."
        ),
        "threshold_text": (
            f"coverage_pct >= {COVERAGE_FLOOR_PCT:.0f}% over last "
            f"{WINDOW_DAYS} days (min {COVERAGE_MIN_EVENTS} events to evaluate)"
        ),
        "metadata": {
            "coverage_floor_pct": COVERAGE_FLOOR_PCT,
            "min_events": COVERAGE_MIN_EVENTS,
        },
    }
