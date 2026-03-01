from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import uuid

script_dir = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(script_dir))
import csi_delivery_slo_gatekeeper as slo_gatekeeper

from csi_ingester.store import source_state as source_state_store
from csi_ingester.store.sqlite import connect, ensure_schema


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _insert_event(
    conn,
    *,
    source: str,
    event_type: str,
    created_at: datetime,
    delivered: int,
) -> str:
    eid = f"evt_{uuid.uuid4().hex[:10]}"
    ts = _ts(created_at)
    conn.execute(
        """
        INSERT INTO events (
            event_id, dedupe_key, source, event_type, occurred_at, received_at,
            subject_json, routing_json, metadata_json, delivered, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            eid,
            f"dk_{eid}",
            source,
            event_type,
            ts,
            ts,
            "{}",
            "{}",
            "{}",
            int(delivered),
            ts,
        ),
    )
    conn.commit()
    return eid


def _insert_attempt(conn, *, event_id: str, created_at: datetime, delivered: int, status_code: int) -> None:
    conn.execute(
        """
        INSERT INTO delivery_attempts (event_id, target, delivered, status_code, attempted_at)
        VALUES (?, 'ua_signals_ingest', ?, ?, ?)
        """,
        (event_id, int(delivered), int(status_code), _ts(created_at)),
    )
    conn.commit()


def _insert_dead_letter(conn, *, source: str, created_at: datetime) -> None:
    conn.execute(
        """
        INSERT INTO dead_letter (event_id, event_json, error_reason, retry_count, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            f"dlq_{uuid.uuid4().hex[:10]}",
            f'{{"source":"{source}"}}',
            "ua_status_500",
            1,
            _ts(created_at),
        ),
    )
    conn.commit()


def test_evaluate_slo_flags_breach_and_top_root_causes(tmp_path: Path):
    db = tmp_path / "csi.db"
    conn = connect(db)
    ensure_schema(conn)

    start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    previous_start = start - timedelta(days=1)
    previous_end = start

    # Force poor delivery ratio.
    rss_event = _insert_event(
        conn,
        source="youtube_channel_rss",
        event_type="channel_new_upload",
        created_at=start + timedelta(hours=10),
        delivered=0,
    )
    _insert_attempt(
        conn,
        event_id=rss_event,
        created_at=start + timedelta(hours=10, minutes=2),
        delivered=0,
        status_code=503,
    )

    # Keep reddit stale by inserting only old data.
    _insert_event(
        conn,
        source="reddit_discovery",
        event_type="subreddit_new_post",
        created_at=previous_start + timedelta(hours=1),
        delivered=1,
    )

    # DLQ growth in current window.
    _insert_dead_letter(conn, source="youtube_channel_rss", created_at=start + timedelta(hours=11))
    _insert_dead_letter(conn, source="reddit_discovery", created_at=start + timedelta(hours=12))

    # Frequent canary regressions.
    for hour in (7, 8, 9):
        _insert_event(
            conn,
            source="csi_analytics",
            event_type="delivery_health_regression",
            created_at=start + timedelta(hours=hour),
            delivered=1,
        )

    evaluation = slo_gatekeeper._evaluate_slo(
        conn,
        start=start,
        end=end,
        previous_start=previous_start,
        previous_end=previous_end,
        min_delivery_success_ratio=0.95,
        max_dlq_backlog=0,
        max_dlq_backlog_delta=0,
        max_source_lag_minutes=180,
        max_canary_regressions=1,
    )
    conn.close()

    assert str(evaluation.get("status") or "") == "breached"
    top = evaluation.get("top_root_causes") if isinstance(evaluation.get("top_root_causes"), list) else []
    assert 1 <= len(top) <= 3
    codes = [str(item.get("code") or "") for item in evaluation.get("root_cause_candidates") or [] if isinstance(item, dict)]
    assert "delivery_success_ratio_below_min" in codes
    assert "dlq_backlog_exceeds_max" in codes
    assert "canary_regression_frequency_exceeds_max" in codes
    assert any(code.startswith("source_freshness_lag_exceeds_max:reddit_discovery") for code in codes)


def test_run_once_supports_backfill_day_and_state_history(tmp_path: Path):
    db = tmp_path / "csi.db"
    conn = connect(db)
    ensure_schema(conn)

    target_day = "2026-03-03"
    start = datetime(2026, 3, 3, 0, 0, 0, tzinfo=timezone.utc)

    # Healthy events + attempts for both watched sources.
    rss_event = _insert_event(
        conn,
        source="youtube_channel_rss",
        event_type="channel_new_upload",
        created_at=start + timedelta(hours=12),
        delivered=1,
    )
    reddit_event = _insert_event(
        conn,
        source="reddit_discovery",
        event_type="subreddit_new_post",
        created_at=start + timedelta(hours=11),
        delivered=1,
    )
    _insert_event(
        conn,
        source="csi_analytics",
        event_type="rss_trend_report",
        created_at=start + timedelta(hours=18),
        delivered=1,
    )
    _insert_attempt(conn, event_id=rss_event, created_at=start + timedelta(hours=12, minutes=3), delivered=1, status_code=200)
    _insert_attempt(conn, event_id=reddit_event, created_at=start + timedelta(hours=11, minutes=4), delivered=1, status_code=200)
    conn.close()

    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    csi_env_file = tmp_path / "csi.env"
    csi_env_file.write_text("", encoding="utf-8")

    args = argparse.Namespace(
        db_path=str(db),
        state_key="runtime_canary:delivery_slo:test",
        day=target_day,
        min_delivery_success_ratio=0.90,
        max_dlq_backlog=0,
        max_dlq_backlog_delta=0,
        max_source_lag_minutes=900,
        max_canary_regressions=2,
        force=False,
        dry_run=True,
        env_file=str(env_file),
        csi_env_file=str(csi_env_file),
    )
    code, summary = slo_gatekeeper._run_once(args)
    assert code == 0
    assert str(summary.get("target_day_utc") or "") == target_day
    assert str(summary.get("status") or "") == "ok"

    conn = connect(db)
    state = source_state_store.get_state(conn, "runtime_canary:delivery_slo:test") or {}
    conn.close()
    assert str(state.get("target_day_utc") or "") == target_day
    assert str(state.get("status") or "") == "ok"
    history = state.get("history") if isinstance(state.get("history"), list) else []
    assert len(history) >= 1
