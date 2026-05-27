"""Tests for vp_mission_backlog snapshot + trend computation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import (
    queue_vp_mission,
    upsert_vp_session,
)
from universal_agent.services.vp_mission_backlog import (
    compute_backlog_snapshot,
    format_backlog_brief,
    prune_backlog_history,
    record_backlog_sample,
    snapshot_to_payload,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_schema(c)
    upsert_vp_session(
        c, vp_id="vp.general.primary", runtime_id="rt.test",
        status="idle", session_id="vp.general.primary.test",
    )
    return c


def _queue(conn, mission_id, mission_type):
    queue_vp_mission(
        conn=conn,
        mission_id=mission_id,
        vp_id="vp.general.primary",
        mission_type=mission_type,
        objective="t",
        payload={},
    )


def test_snapshot_empty_when_no_missions(conn):
    snap = compute_backlog_snapshot(conn)
    assert snap.tiers == ()
    assert snap.total_queued() == 0
    assert snap.total_running() == 0


def test_snapshot_groups_by_tier(conn):
    _queue(conn, "m1", "briefing")
    _queue(conn, "m2", "briefing")
    _queue(conn, "m3", "insight_brief")
    _queue(conn, "m4", "curation")

    snap = compute_backlog_snapshot(conn)
    by_tier = snap.queued_by_tier()
    assert by_tier["operator_daily"] == 2
    assert by_tier["operator_signal"] == 1
    assert by_tier["maintenance"] == 1
    assert by_tier["background"] == 0
    assert snap.total_queued() == 4


def test_record_sample_round_trips(conn):
    _queue(conn, "m1", "briefing")
    snap = compute_backlog_snapshot(conn)
    record_backlog_sample(conn, snap)

    rows = conn.execute(
        "SELECT vp_id, priority_tier, queued_count FROM vp_mission_backlog_history"
    ).fetchall()
    assert len(rows) == 1
    assert dict(rows[0])["queued_count"] == 1
    assert dict(rows[0])["priority_tier"] == "operator_daily"


def test_trend_increasing_when_current_exceeds_prev_by_threshold(conn):
    # Manually seed a historical sample showing the backlog WAS small.
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat()
    conn.execute(
        """INSERT INTO vp_mission_backlog_history
           (measured_at, vp_id, priority_tier, queued_count, running_count)
           VALUES (?, ?, ?, ?, ?)""",
        (old_ts, "vp.general.primary", "operator_signal", 1, 0),
    )
    conn.commit()

    # Now queue 5 insight briefs — that's +5 vs the historical sample,
    # well over the increase threshold of 3.
    for i in range(5):
        _queue(conn, f"m-insight-{i}", "insight_brief")

    snap = compute_backlog_snapshot(conn)
    trend = next(
        t for t in snap.trends
        if t.priority_tier == "operator_signal"
    )
    assert trend.trend_30m == "increasing"
    assert trend.current_queued == 5
    assert trend.prev_queued_30m == 1


def test_trend_decreasing_when_current_below_prev_by_threshold(conn):
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat()
    conn.execute(
        """INSERT INTO vp_mission_backlog_history
           (measured_at, vp_id, priority_tier, queued_count, running_count)
           VALUES (?, ?, ?, ?, ?)""",
        (old_ts, "vp.general.primary", "operator_signal", 10, 0),
    )
    conn.commit()

    _queue(conn, "m-insight-1", "insight_brief")  # only 1 queued now

    snap = compute_backlog_snapshot(conn)
    trend = next(
        t for t in snap.trends
        if t.priority_tier == "operator_signal"
    )
    assert trend.trend_30m == "decreasing"


def test_trend_stable_when_within_threshold(conn):
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat()
    conn.execute(
        """INSERT INTO vp_mission_backlog_history
           (measured_at, vp_id, priority_tier, queued_count, running_count)
           VALUES (?, ?, ?, ?, ?)""",
        (old_ts, "vp.general.primary", "operator_signal", 3, 0),
    )
    conn.commit()

    # +2 from baseline = stable (under +3 threshold)
    _queue(conn, "m1", "insight_brief")
    _queue(conn, "m2", "insight_brief")
    _queue(conn, "m3", "insight_brief")
    _queue(conn, "m4", "insight_brief")
    _queue(conn, "m5", "insight_brief")

    snap = compute_backlog_snapshot(conn)
    trend = next(t for t in snap.trends if t.priority_tier == "operator_signal")
    assert trend.trend_30m == "stable"


def test_trend_no_history_when_first_sample(conn):
    _queue(conn, "m1", "briefing")
    snap = compute_backlog_snapshot(conn)
    trend = next(t for t in snap.trends if t.priority_tier == "operator_daily")
    assert trend.trend_30m == "no_history"
    assert trend.prev_queued_30m is None


def test_format_backlog_brief_renders_when_active(conn):
    _queue(conn, "m1", "briefing")
    _queue(conn, "m2", "insight_brief")
    snap = compute_backlog_snapshot(conn)
    md = format_backlog_brief(snap)
    assert "VP Mission Backlog" in md
    assert "operator_daily" in md
    assert "operator_signal" in md
    assert "vp.general.primary" in md


def test_format_backlog_brief_empty_when_idle(conn):
    snap = compute_backlog_snapshot(conn)
    md = format_backlog_brief(snap)
    assert "No active queue" in md


def test_snapshot_to_payload_is_json_serializable(conn):
    import json
    _queue(conn, "m1", "briefing")
    snap = compute_backlog_snapshot(conn)
    payload = snapshot_to_payload(snap)
    # Must serialize cleanly — this payload goes through proactive_health
    # JSON responses + Simone's context block.
    json.dumps(payload)
    assert payload["total_queued"] == 1
    assert payload["queued_by_tier"]["operator_daily"] == 1


def test_prune_removes_old_history_rows(conn):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    for ts in (old_ts, recent_ts):
        conn.execute(
            """INSERT INTO vp_mission_backlog_history
               (measured_at, vp_id, priority_tier, queued_count, running_count)
               VALUES (?, ?, ?, ?, ?)""",
            (ts, "vp.general.primary", "operator_daily", 1, 0),
        )
    conn.commit()

    deleted = prune_backlog_history(conn, older_than_days=14)
    assert deleted == 1
    remaining = conn.execute(
        "SELECT COUNT(*) FROM vp_mission_backlog_history"
    ).fetchone()[0]
    assert remaining == 1
