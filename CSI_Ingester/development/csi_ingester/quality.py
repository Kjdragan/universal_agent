"""Source-quality scoring for CSI sources.

Pure, read-only scorers over the analysis tables. Extracted from
``scripts/csi_source_quality_assessment.py`` so the identical logic runs both as
the standalone operator tool (manual ``--dry-run`` inspection over a read-only
connection) and *in-process* inside the ingester's periodic scheduler
(``CSIService._run_source_quality``).

Running in-process matters: the live ingester is the sole continuous writer of
the canonical ``csi.db`` and holds the WAL write lock, so an external batch
writer can't acquire a write window (verified: ``BEGIN IMMEDIATE`` → SQLITE_BUSY
after 30s). Writing through the ingester's own connection sidesteps that
entirely.

These functions only issue ``SELECT`` queries and return plain dicts — the
caller decides whether/where to persist the results.
"""

from __future__ import annotations

import sqlite3


def score_youtube_channels(conn: sqlite3.Connection, lookback_days: int) -> dict[str, dict]:
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


def score_threads_terms(conn: sqlite3.Connection, lookback_days: int) -> dict[str, dict]:
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
