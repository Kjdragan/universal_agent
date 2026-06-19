#!/usr/bin/env python3
"""CSI Source Quality Assessment — evening batch job.

Scores each data source (YouTube channels, Threads terms) based on recent
analysis outputs, and promotes/demotes tiers accordingly.

Designed to run as a systemd timer job (e.g., evening batch window).

Usage:
    uv run python scripts/csi_source_quality_assessment.py [--lookback-days 7] [--dry-run]
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys

# Ensure csi_ingester package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from csi_ingester.store.source_manager import (
    auto_promote_demote,
    get_source_summary,
    record_quality_assessment,
)
from csi_ingester.store.sqlite import connect, ensure_schema
from csi_ingester.quality import (
    score_threads_terms as _score_threads_terms,
    score_youtube_channels as _score_youtube_channels,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("csi_source_quality")


# ── Main ────────────────────────────────────────────────────────────────────

def run_assessment(db_path: str, lookback_days: int, dry_run: bool = False) -> dict:
    """Run quality assessment across all source types."""
    # connect() expects a Path (its first line does db_path.parent.mkdir). The
    # unit passes --db-path / CSI_DB_PATH as a plain str, so coerce here — this
    # script is the lone str-caller; all other connect() callers already pass a
    # Path. Without this the service crashed with AttributeError every daily run.
    conn = connect(Path(db_path))
    ensure_schema(conn)

    results = {
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "youtube": {"scored": 0, "promoted": [], "demoted": []},
        "threads": {"scored": 0, "promoted": [], "demoted": []},
    }

    # ── YouTube channels ──
    yt_scores = _score_youtube_channels(conn, lookback_days)
    for channel_id, metrics in yt_scores.items():
        if not dry_run:
            record_quality_assessment(
                conn,
                source_type="youtube",
                source_key=channel_id,
                relevance=metrics["relevance"],
                engagement=metrics["engagement"],
                novelty=metrics["novelty"],
                confidence=metrics["confidence"],
                items_count=metrics["items_count"],
            )
        logger.info(
            "YouTube %s (%s): rel=%.2f eng=%.2f nov=%.2f conf=%.2f items=%d",
            channel_id,
            metrics.get("channel_name", ""),
            metrics["relevance"],
            metrics["engagement"],
            metrics["novelty"],
            metrics["confidence"],
            metrics["items_count"],
        )
    results["youtube"]["scored"] = len(yt_scores)

    if not dry_run and yt_scores:
        pd = auto_promote_demote(conn, source_type="youtube")
        results["youtube"]["promoted"] = pd["promoted"]
        results["youtube"]["demoted"] = pd["demoted"]

    # ── Threads search terms ──
    threads_scores = _score_threads_terms(conn, lookback_days)
    for term, metrics in threads_scores.items():
        if not dry_run:
            record_quality_assessment(
                conn,
                source_type="threads",
                source_key=term,
                relevance=metrics["relevance"],
                engagement=metrics["engagement"],
                novelty=metrics["novelty"],
                confidence=metrics["confidence"],
                items_count=metrics["items_count"],
            )
        logger.info(
            "Threads '%s': rel=%.2f eng=%.2f nov=%.2f conf=%.2f items=%d",
            term,
            metrics["relevance"],
            metrics["engagement"],
            metrics["novelty"],
            metrics["confidence"],
            metrics["items_count"],
        )
    results["threads"]["scored"] = len(threads_scores)

    if not dry_run and threads_scores:
        pd = auto_promote_demote(conn, source_type="threads")
        results["threads"]["promoted"] = pd["promoted"]
        results["threads"]["demoted"] = pd["demoted"]

    # ── Summary ──
    summary = get_source_summary(conn)
    results["summary"] = summary
    conn.close()
    return results


def main():
    parser = argparse.ArgumentParser(description="CSI Source Quality Assessment")
    parser.add_argument(
        "--db-path",
        default=os.getenv("CSI_DB_PATH", "var/csi.db"),
        help="Path to CSI SQLite database",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=7,
        help="Number of days to look back for analysis data (default: 7)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Score sources but don't write to DB or change tiers",
    )
    args = parser.parse_args()

    logger.info(
        "Starting quality assessment db=%s lookback=%dd dry_run=%s",
        args.db_path,
        args.lookback_days,
        args.dry_run,
    )

    results = run_assessment(args.db_path, args.lookback_days, args.dry_run)

    # Print summary
    for source_type in ("youtube", "threads"):
        r = results[source_type]
        promoted = ", ".join(r["promoted"]) if r["promoted"] else "none"
        demoted = ", ".join(r["demoted"]) if r["demoted"] else "none"
        logger.info(
            "%s: scored=%d promoted=[%s] demoted=[%s]",
            source_type,
            r["scored"],
            promoted,
            demoted,
        )

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
