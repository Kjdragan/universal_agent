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
import os
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

# Enrichment-coverage thresholds.  Below COVERAGE_FLOOR_PCT of *in-scope* events
# (the ones the selective enricher is supposed to process) having a matching
# rss_event_analysis row over the last 7 days, the pipeline is considered
# broken.  The MIN_EVENTS guard keeps small-sample windows quiet.
COVERAGE_FLOOR_PCT = 50.0
COVERAGE_MIN_EVENTS = 5

# ── Enricher eligibility mirror (keep in sync with csi_rss_semantic_enrich.py) ──
# As of PR #660 (2026-06-01) the RSS semantic enricher is *selective*: each run
# only processes events that are ``delivered = 1`` and NOT on a channel whose
# analysis history is majority non-domain (plus a small always-keep allowlist),
# newest-first, capped at ``--max-events``.  A flat events-vs-analysis coverage
# check therefore reports a misleadingly low number (undelivered + intentionally
# skipped channels never get a row) and fires a false CRITICAL.  To measure
# something meaningful we mirror the enricher's eligibility here and compute
# coverage only over IN-SCOPE events.
#
# SINGLE SOURCE OF TRUTH: the canonical definition of these categories lives in
# the enricher (CSI_Ingester/development/scripts/csi_rss_semantic_enrich.py
# ::_DOMAIN_CATS / _DEFAULT_ALWAYS_KEEP / _nondomain_skip_names).  The copy below
# MUST stay identical; a static drift-guard test
# (tests/unit/test_youtube_transcript_coverage_invariant.py::test_domain_cats_in_sync_with_enricher)
# parses the enricher source and fails CI if the two diverge — so adding e.g.
# "geopolitics" to the enricher's domain set forces the same edit here.
_DOMAIN_CATS = {
    "ai_coding",
    "ai_models",
    "ai_news_and_business",
    "ai_business",
    "ai_applications",
    "software_engineering",
    "technology",
}

# Mirror of the enricher's _DEFAULT_ALWAYS_KEEP / CSI_RSS_SELECTION_ALWAYS_KEEP.
# These channels are exempt from the non-domain skip even with a majority
# non-domain history (operator-pinned).
_DEFAULT_ALWAYS_KEEP = "Pyotr Kurzin | Geopolitics,Jake Broe"


def _always_keep_names() -> set[str]:
    raw = os.environ.get("CSI_RSS_SELECTION_ALWAYS_KEEP", _DEFAULT_ALWAYS_KEEP)
    return {n.strip() for n in raw.split(",") if n.strip()}


def _nondomain_skip_names(db: sqlite3.Connection) -> set[str]:
    """Channels whose analysis history is majority non-domain (>= 2 analyses).

    Mirror of ``csi_rss_semantic_enrich.py::_nondomain_skip_names`` so the
    coverage invariant scopes itself to exactly the events the enricher would
    process.  Returns the empty set on any query error (fail-open: scope to all
    delivered events rather than crash the heartbeat).
    """
    agg: Dict[str, list[int]] = {}  # name -> [nondomain, total]
    try:
        rows = db.execute(
            "SELECT channel_name, category FROM rss_event_analysis "
            "WHERE source='youtube_channel_rss' AND category IS NOT NULL AND category != ''"
        ).fetchall()
    except sqlite3.Error:
        return set()
    for row in rows:
        name = row["channel_name"]
        if not name:
            # A NULL/empty channel_name can't be matched against subject_json's
            # channel_name anyway, and mixing None into the set would break the
            # later sorted() (str vs None).  Enricher events are reliably
            # channel-named; skip the degenerate row.
            continue
        category = row["category"]
        bucket = agg.setdefault(name, [0, 0])
        bucket[1] += 1
        if category not in _DOMAIN_CATS:
            bucket[0] += 1
    skip = {n for n, (nd, total) in agg.items() if total >= 2 and nd > (total - nd)}
    return skip - _always_keep_names()


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
        "Of the YouTube events the selective enricher is supposed to process in "
        f"the last 7 days (delivered, on domain channels), at least "
        f"{COVERAGE_FLOOR_PCT:.0f}% must have a matching rss_event_analysis row. "
        "Mirrors the enricher's eligibility (PR #660: delivered=1 + skip "
        "majority-non-domain channels) so intentionally-skipped channels and "
        "undelivered events don't trip a false CRITICAL, while still catching the "
        "original failure mode where enrichment stopped writing the rows it owns."
    ),
    severity="critical",
    runbook_command=(
        "sqlite3 \"$UA_CSI_DB_PATH\" \"SELECT COUNT(*) in_scope_events, "
        "SUM(CASE WHEN a.event_id IS NOT NULL THEN 1 ELSE 0 END) enriched "
        "FROM events e LEFT JOIN rss_event_analysis a ON a.event_id = e.event_id "
        "WHERE e.source='youtube_channel_rss' AND e.delivered=1 AND e.occurred_at >= "
        "datetime('now','-7 days');\"  "
        "# note: majority-non-domain channels are further excluded in-code "
        "(see _nondomain_skip_names)"
    ),
    metadata={
        "pipeline": "youtube_daily_digest + csi_rss_semantic_enrich",
        "tables": ["events", "rss_event_analysis"],
        "doc": "project_docs/04_intelligence/05_youtube_csi_flow.md",
    },
)
def youtube_enrichment_coverage(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Probe for the empty-rss_event_analysis failure mode, scoped to IN-SCOPE events.

    Joins ``events`` (the ingest-side table) against ``rss_event_analysis`` (the
    enrichment-side table), but — since PR #660 made the enricher *selective* —
    only over the events the enricher actually owns: ``delivered = 1`` and NOT on
    a majority-non-domain channel (mirroring ``_nondomain_skip_names``).  When
    ingest succeeded but enrichment never wrote for those events — the original
    38/38 incident, or a stalled/dead enricher — in-scope events exist with no
    match and coverage collapses below the floor.  Undelivered events and
    intentionally-skipped non-domain channels are excluded so the steady-state
    selective behaviour does not read as a false CRITICAL.
    """
    csi_db_path = _resolve_csi_db_path(ctx)
    if csi_db_path is None:
        return None

    db = sqlite3.connect(f"file:{csi_db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        skip = sorted(_nondomain_skip_names(db))
        # The WINDOW_DAYS bound is the invariant's OWN denominator choice — the
        # enricher itself is unbounded-time, newest-first, and capped at
        # --max-events.  At today's _DOMAIN_CATS the in-scope set reads ~96%
        # healthy, but if _DOMAIN_CATS is widened so in-scope arrivals exceed
        # enricher throughput, newest-first ordering can leave older in-window
        # events permanently unenriched and park coverage near the floor.  Widen
        # _DOMAIN_CATS only alongside a throughput bump (see
        # project_docs/04_intelligence/05_youtube_csi_flow.md B.4).
        params: list[Any] = [f"-{WINDOW_DAYS} days"]
        skip_clause = ""
        if skip:
            skip_clause = (
                "AND COALESCE(json_extract(e.subject_json, '$.channel_name'), '') "
                "NOT IN (%s) " % ",".join("?" for _ in skip)
            )
            params += skip
        row = db.execute(
            f"""
            SELECT
                COUNT(*) AS total_events,
                SUM(CASE WHEN a.event_id IS NOT NULL THEN 1 ELSE 0 END) AS enriched_events
            FROM events e
            LEFT JOIN rss_event_analysis a ON a.event_id = e.event_id
            WHERE e.source = 'youtube_channel_rss'
              AND e.delivered = 1
              AND e.occurred_at >= datetime('now', ?)
              {skip_clause}
            """,
            params,
        ).fetchone()
    except sqlite3.Error as exc:
        # `events`/`subject_json` can legitimately be absent on a fresh dev box.
        # Treat as N/A rather than surfacing a probe_error every heartbeat.
        logger.debug("youtube_enrichment_coverage: query failed (%s); skipping", exc)
        return None
    finally:
        db.close()

    total = int((row["total_events"] if row else 0) or 0)
    enriched = int((row["enriched_events"] if row else 0) or 0)

    if total < COVERAGE_MIN_EVENTS:
        # Too few in-scope events to make a useful claim.  Stay quiet.
        return None

    coverage_pct = (100.0 * enriched / total) if total else 0.0
    if coverage_pct >= COVERAGE_FLOOR_PCT:
        return None

    return {
        "observed_value": {
            "in_scope_events": total,
            "enriched_events": enriched,
            "coverage_pct": round(coverage_pct, 1),
            "skipped_nondomain_channels": len(skip),
            "window_days": WINDOW_DAYS,
        },
        "message": (
            f"{enriched} of {total} in-scope YouTube events in the last "
            f"{WINDOW_DAYS} days have a matching rss_event_analysis row "
            f"(coverage {coverage_pct:.1f}% < floor {COVERAGE_FLOOR_PCT:.0f}%). "
            "In-scope = delivered events on domain channels the selective "
            "enricher (csi_rss_semantic_enrich.py, PR #660) actually processes; "
            f"{len(skip)} majority-non-domain channel(s) and undelivered events "
            "are intentionally excluded.  A low value means the enricher is "
            "failing to write the events it owns (stopped, erroring, or far "
            "behind) — not that non-domain channels are unenriched."
        ),
        "threshold_text": (
            f"in-scope coverage_pct >= {COVERAGE_FLOOR_PCT:.0f}% over last "
            f"{WINDOW_DAYS} days (min {COVERAGE_MIN_EVENTS} in-scope events to evaluate)"
        ),
        "metadata": {
            "coverage_floor_pct": COVERAGE_FLOOR_PCT,
            "min_events": COVERAGE_MIN_EVENTS,
            "scope": "delivered=1 AND domain-channel (mirrors enricher eligibility)",
        },
    }
