from __future__ import annotations

import json
import uuid
from pathlib import Path
import sys

script_dir = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(script_dir))
import csi_rss_quality_gate

from csi_ingester.store.sqlite import connect, ensure_schema


def _insert_event(conn, *, source: str, delivered: int = 1) -> None:
    eid = f"evt_{uuid.uuid4().hex[:10]}"
    conn.execute(
        """
        INSERT INTO events (
            event_id, dedupe_key, source, event_type, occurred_at, received_at,
            subject_json, routing_json, metadata_json, delivered
        ) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'), ?, ?, ?, ?)
        """,
        (
            eid,
            f"dk_{eid}",
            source,
            "channel_new_upload",
            "{}",
            "{}",
            "{}",
            int(delivered),
        ),
    )
    conn.commit()


def _insert_dead_letter(conn, *, source: str) -> None:
    conn.execute(
        """
        INSERT INTO dead_letter (event_id, event_json, error_reason, retry_count)
        VALUES (?, ?, ?, ?)
        """,
        (
            f"dlq_{uuid.uuid4().hex[:10]}",
            json.dumps({"source": source, "event_type": "channel_new_upload"}),
            "ua_status_500",
            1,
        ),
    )
    conn.commit()


def test_recent_rss_dlq_count_filters_to_rss_source(tmp_path: Path):
    db = tmp_path / "csi.db"
    conn = connect(db)
    ensure_schema(conn)
    _insert_dead_letter(conn, source="youtube_channel_rss")
    _insert_dead_letter(conn, source="reddit_discovery")

    count = csi_rss_quality_gate._recent_rss_dlq_count(conn, "-6 hours")
    assert count == 1

    conn.close()


def test_get_metrics_reports_rss_scoped_dlq(tmp_path: Path):
    db = tmp_path / "csi.db"
    conn = connect(db)
    ensure_schema(conn)
    _insert_event(conn, source="youtube_channel_rss", delivered=0)
    _insert_event(conn, source="reddit_discovery", delivered=0)
    _insert_dead_letter(conn, source="youtube_channel_rss")
    _insert_dead_letter(conn, source="reddit_discovery")

    metrics = csi_rss_quality_gate._get_metrics(conn, window_hours=6)
    assert metrics["rss_events_recent"] == 1
    assert metrics["rss_undelivered_recent"] == 1
    assert metrics["dlq_recent"] == 1

    conn.close()
