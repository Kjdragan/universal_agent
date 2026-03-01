from __future__ import annotations

from pathlib import Path
import sys
import uuid

script_dir = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(script_dir))
import csi_delivery_health_canary

from csi_ingester.store import source_state as source_state_store
from csi_ingester.store.sqlite import connect, ensure_schema


def _insert_event(conn, *, source: str, delivered: int = 1) -> str:
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
    return eid


def _insert_attempt(conn, *, event_id: str, delivered: int, status_code: int) -> None:
    conn.execute(
        """
        INSERT INTO delivery_attempts (event_id, target, delivered, status_code, attempted_at)
        VALUES (?, 'ua_signals_ingest', ?, ?, datetime('now'))
        """,
        (event_id, int(delivered), int(status_code)),
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
            f'{{"source":"{source}","event_type":"channel_new_upload"}}',
            "ua_status_500",
            1,
        ),
    )
    conn.commit()


def test_canary_transition_matrix():
    opened = csi_delivery_health_canary._canary_transition(
        previous_status="ok",
        current_status="failing",
        previous_alert_epoch=0,
        now_epoch=1000,
        repeat_minutes=45,
        force=False,
    )
    assert opened["emit"] is True
    assert opened["event_type"] == "delivery_health_regression"
    assert opened["reason"] == "opened"

    reminder = csi_delivery_health_canary._canary_transition(
        previous_status="failing",
        current_status="failing",
        previous_alert_epoch=1000,
        now_epoch=4000,
        repeat_minutes=45,
        force=False,
    )
    assert reminder["emit"] is True
    assert reminder["event_type"] == "delivery_health_regression"
    assert reminder["reason"] == "reminder"

    suppressed = csi_delivery_health_canary._canary_transition(
        previous_status="failing",
        current_status="failing",
        previous_alert_epoch=3900,
        now_epoch=4000,
        repeat_minutes=45,
        force=False,
    )
    assert suppressed["emit"] is False
    assert suppressed["reason"] == "no_change"

    recovered = csi_delivery_health_canary._canary_transition(
        previous_status="degraded",
        current_status="ok",
        previous_alert_epoch=0,
        now_epoch=5000,
        repeat_minutes=45,
        force=False,
    )
    assert recovered["emit"] is True
    assert recovered["event_type"] == "delivery_health_recovered"
    assert recovered["reason"] == "recovered"


def test_evaluate_delivery_health_builds_guided_remediation(tmp_path: Path):
    db = tmp_path / "csi.db"
    conn = connect(db)
    ensure_schema(conn)

    # RSS source has failed delivery + DLQ.
    rss_event_id = _insert_event(conn, source="youtube_channel_rss", delivered=0)
    _insert_attempt(conn, event_id=rss_event_id, delivered=0, status_code=500)
    _insert_dead_letter(conn, source="youtube_channel_rss")

    # Reddit has no events and should be marked low-volume degraded.
    result = csi_delivery_health_canary._evaluate_delivery_health(
        conn,
        window_hours=6,
        stale_minutes=240,
        max_failed_attempt_ratio=0.2,
        min_rss_events=1,
        min_reddit_events=1,
        max_dlq_recent=0,
        adapter_failures_threshold=3,
    )
    conn.close()

    assert result["status"] in {"degraded", "failing"}
    codes = {str(step.get("code") or "") for step in result["remediation_steps"]}
    assert "delivery_failures_detected" in codes
    assert "dlq_backlog_exceeds_threshold" in codes
    assert "reddit_source_stale_or_low_volume" in codes


def test_evaluate_delivery_health_marks_adapter_failures_as_failing(tmp_path: Path):
    db = tmp_path / "csi.db"
    conn = connect(db)
    ensure_schema(conn)
    source_state_store.set_state(
        conn,
        "adapter_health:youtube_channel_rss",
        {
            "adapter": "youtube_channel_rss",
            "consecutive_failures": 5,
            "last_error": "timeout",
            "status": "error",
        },
    )

    result = csi_delivery_health_canary._evaluate_delivery_health(
        conn,
        window_hours=6,
        stale_minutes=240,
        max_failed_attempt_ratio=0.2,
        min_rss_events=0,
        min_reddit_events=0,
        max_dlq_recent=0,
        adapter_failures_threshold=3,
    )
    conn.close()

    rss_row = next(item for item in result["sources"] if str(item.get("source")) == "youtube_channel_rss")
    assert str(rss_row["status"]) == "failing"
    codes = {str(step.get("code") or "") for step in result["remediation_steps"]}
    assert "adapter_consecutive_failures" in codes

