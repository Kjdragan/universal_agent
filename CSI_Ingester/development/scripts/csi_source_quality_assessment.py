#!/usr/bin/env python3
"""CSI Source Quality Assessment — evening batch job.

Scores each data source (YouTube channels, Reddit subreddits, Threads terms)
based on recent analysis outputs, and promotes/demotes tiers accordingly.

Designed to run as a systemd timer job (e.g., evening batch window).

Usage:
    uv run python scripts/csi_source_quality_assessment.py [--lookback-days 7] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure csi_ingester package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from csi_ingester.store.sqlite import connect, ensure_schema
from csi_ingester.store.source_manager import (
    auto_promote_demote,
    get_source_summary,
    record_quality_assessment,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("csi_source_quality")


# ── Quality Metric Extractors ───────────────────────────────────────────────

def _score_youtube_channels(conn, lookback_days: int) -> dict[str, dict]:
    """Score each YouTube channel from rss_event_analysis data."""
    rows = conn.execute(
        """
        SELECT channel_id, channel_name,
               COUNT(*) AS total,
               SUM(CASE WHEN category NOT IN ('other_signal', 'other_interest') THEN 1 ELSE 0 END) AS relevant,
               SUM(CASE WHEN transcript_status = 'ok' THEN 1 ELSE 0 END) AS with_transcript,
               SUM(CASE WHEN summary_text IS NOT NULL AND summary_text != '' THEN 1 ELSE 0 END) AS with_summary,
               AVG(CASE WHEN total_tokens > 0 THEN 1.0 ELSE 0.0 END) AS analysis_rate,
               MAX(analyzed_at) AS latest_analysis
        FROM rss_event_analysis
        WHERE analyzed_at >= datetime('now', ?)
        GROUP BY channel_id
        """,
        (f"-{lookback_days} days",),
    ).fetchall()

    scores = {}
    for row in rows:
        channel_id = row["channel_id"]
        total = max(row["total"], 1)
        relevance = row["relevant"] / total
        engagement = min(1.0, row["with_transcript"] / total)
        novelty = row["analysis_rate"] if row["analysis_rate"] else 0.0
        confidence = min(1.0, total / 10.0)  # More items → higher confidence

        scores[channel_id] = {
            "relevance": round(relevance, 3),
            "engagement": round(engagement, 3),
            "novelty": round(novelty, 3),
            "confidence": round(confidence, 3),
            "items_count": total,
            "channel_name": row["channel_name"] or "",
        }
    return scores


def _score_reddit_sources(conn, lookback_days: int) -> dict[str, dict]:
    """Score each subreddit from reddit_event_analysis data."""
    rows = conn.execute(
        """
        SELECT subreddit,
               COUNT(*) AS total,
               SUM(CASE WHEN category NOT IN ('other_signal', 'other_interest') THEN 1 ELSE 0 END) AS relevant,
               AVG(score) AS avg_score,
               AVG(num_comments) AS avg_comments,
               SUM(CASE WHEN summary_text IS NOT NULL AND summary_text != '' THEN 1 ELSE 0 END) AS with_summary,
               MAX(analyzed_at) AS latest_analysis
        FROM reddit_event_analysis
        WHERE analyzed_at >= datetime('now', ?)
        GROUP BY subreddit
        """,
        (f"-{lookback_days} days",),
    ).fetchall()

    scores = {}
    for row in rows:
        subreddit = row["subreddit"]
        total = max(row["total"], 1)
        relevance = row["relevant"] / total
        # Engagement: normalize reddit score (100+ is good) and comments (10+ is good)
        avg_score = row["avg_score"] or 0
        avg_comments = row["avg_comments"] or 0
        engagement = min(1.0, (avg_score / 100.0 + avg_comments / 10.0) / 2)
        novelty = row["with_summary"] / total if total > 0 else 0.0
        confidence = min(1.0, total / 20.0)

        scores[subreddit] = {
            "relevance": round(relevance, 3),
            "engagement": round(engagement, 3),
            "novelty": round(novelty, 3),
            "confidence": round(confidence, 3),
            "items_count": total,
        }
    return scores


def _score_threads_terms(conn, lookback_days: int) -> dict[str, dict]:
    """Score each search term from threads_event_analysis data."""
    rows = conn.execute(
        """
        SELECT query_term,
               COUNT(*) AS total,
               SUM(CASE WHEN category NOT IN ('other_signal', 'other_interest') THEN 1 ELSE 0 END) AS relevant,
               AVG(like_count) AS avg_likes,
               AVG(reply_count) AS avg_replies,
               SUM(CASE WHEN summary_text IS NOT NULL AND summary_text != '' THEN 1 ELSE 0 END) AS with_summary,
               MAX(analyzed_at) AS latest_analysis
        FROM threads_event_analysis
        WHERE analyzed_at >= datetime('now', ?)
          AND query_term IS NOT NULL
          AND query_term != ''
        GROUP BY query_term
        """,
        (f"-{lookback_days} days",),
    ).fetchall()

    scores = {}
    for row in rows:
        term = row["query_term"]
        total = max(row["total"], 1)
        relevance = row["relevant"] / total
        avg_likes = row["avg_likes"] or 0
        avg_replies = row["avg_replies"] or 0
        engagement = min(1.0, (avg_likes / 50.0 + avg_replies / 5.0) / 2)
        novelty = row["with_summary"] / total if total > 0 else 0.0
        confidence = min(1.0, total / 15.0)

        scores[term] = {
            "relevance": round(relevance, 3),
            "engagement": round(engagement, 3),
            "novelty": round(novelty, 3),
            "confidence": round(confidence, 3),
            "items_count": total,
        }
    return scores


# ── Main ────────────────────────────────────────────────────────────────────

def run_assessment(db_path: str, lookback_days: int, dry_run: bool = False) -> dict:
    """Run quality assessment across all source types."""
    conn = connect(db_path)
    ensure_schema(conn)

    results = {
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "youtube": {"scored": 0, "promoted": [], "demoted": []},
        "reddit": {"scored": 0, "promoted": [], "demoted": []},
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

    # ── Reddit subreddits ──
    reddit_scores = _score_reddit_sources(conn, lookback_days)
    for subreddit, metrics in reddit_scores.items():
        if not dry_run:
            record_quality_assessment(
                conn,
                source_type="reddit",
                source_key=subreddit,
                relevance=metrics["relevance"],
                engagement=metrics["engagement"],
                novelty=metrics["novelty"],
                confidence=metrics["confidence"],
                items_count=metrics["items_count"],
            )
        logger.info(
            "Reddit r/%s: rel=%.2f eng=%.2f nov=%.2f conf=%.2f items=%d",
            subreddit,
            metrics["relevance"],
            metrics["engagement"],
            metrics["novelty"],
            metrics["confidence"],
            metrics["items_count"],
        )
    results["reddit"]["scored"] = len(reddit_scores)

    if not dry_run and reddit_scores:
        pd = auto_promote_demote(conn, source_type="reddit")
        results["reddit"]["promoted"] = pd["promoted"]
        results["reddit"]["demoted"] = pd["demoted"]

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
    for source_type in ("youtube", "reddit", "threads"):
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
