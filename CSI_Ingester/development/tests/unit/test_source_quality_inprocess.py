"""Tests for the in-process source-quality scheduler task.

``CSIService._run_source_quality`` must score sources from the analysis tables
and persist the results through the ingester's own connection — the whole point
of moving this in-process (an external writer fails with SQLITE_BUSY against the
live, continuously-written DB). These tests exercise the method directly against
a temp DB; ``asyncio_mode = "auto"`` lets the coroutine tests run without a marker.
"""

from __future__ import annotations

from pathlib import Path

from csi_ingester.config import CSIConfig
from csi_ingester.metrics import MetricsRegistry
from csi_ingester.service import CSIService
from csi_ingester.store.sqlite import connect, ensure_schema


def _build_service(tmp_path: Path):
    config = CSIConfig(
        raw={
            "csi": {"instance_id": "csi-test"},
            "storage": {"db_path": str(tmp_path / "csi.db")},
            "sources": {},
            "delivery": {},
        }
    )
    conn = connect(tmp_path / "csi.db")
    ensure_schema(conn)
    return CSIService(config=config, conn=conn, metrics=MetricsRegistry()), conn


def _insert_rss_analysis(
    conn, *, event_id, channel_id, channel_name, category, transcript_status, summary, tokens
):
    conn.execute(
        """
        INSERT INTO rss_event_analysis
            (event_id, channel_id, channel_name, category,
             transcript_status, summary_text, total_tokens, analyzed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (event_id, channel_id, channel_name, category, transcript_status, summary, tokens),
    )
    conn.commit()


async def test_run_source_quality_writes_history(tmp_path):
    service, conn = _build_service(tmp_path)
    try:
        # Two recent, relevant, transcribed videos for one channel.
        _insert_rss_analysis(
            conn, event_id="e1", channel_id="UC_AAA", channel_name="Alpha AI",
            category="ai_dev", transcript_status="ok", summary="s1", tokens=1200,
        )
        _insert_rss_analysis(
            conn, event_id="e2", channel_id="UC_AAA", channel_name="Alpha AI",
            category="ai_dev", transcript_status="ok", summary="s2", tokens=900,
        )

        await service._run_source_quality()

        rows = conn.execute(
            "SELECT source_type, source_key, score, items_count, relevance "
            "FROM source_quality_history"
        ).fetchall()
        assert len(rows) == 1, "expected exactly one youtube channel score row"
        row = rows[0]
        assert row["source_type"] == "youtube"
        assert row["source_key"] == "UC_AAA"
        assert row["items_count"] == 2
        assert 0.0 <= row["score"] <= 1.0
        assert row["relevance"] == 1.0  # both videos are relevant (non-other category)

        # cycle counted, no errors swallowed
        assert service.metrics.counters["csi.source_quality.cycles"] == 1
        assert service.metrics.counters["csi.source_quality.errors"] == 0
    finally:
        conn.close()


async def test_run_source_quality_empty_db_is_noop(tmp_path):
    service, conn = _build_service(tmp_path)
    try:
        await service._run_source_quality()
        count = conn.execute("SELECT COUNT(*) FROM source_quality_history").fetchone()[0]
        assert count == 0
        # Still a successful cycle (nothing to score), not an error.
        assert service.metrics.counters["csi.source_quality.cycles"] == 1
        assert service.metrics.counters["csi.source_quality.errors"] == 0
    finally:
        conn.close()
