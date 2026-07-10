"""Regression: VP missions orphaned as ``queued`` + ``cancel_requested=1``.

Cancellation requested on a not-yet-leased queued mission can never reach the
terminal ``cancelled`` state on its own — the dispatcher never re-leases a
``cancel_requested`` mission, so no worker ever picks it up. The orphan class
is invisible to ``flush_vp_mission_backlog._list_queued`` (filters
``cancel_requested=0``) and to ``check_stuck_vp_missions`` (scopes to
``dispatched/running``).
``reap_stale_cancel_requested_queued_vp_missions`` reconciles them
idempotently past a TTL race-guard.

Mirrors the shape of ``tests/unit/test_youtube_orphan_run_cleanup.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

from universal_agent.durable.migrations import ensure_schema
from universal_agent.services.vp_mission_reaper import (
    DEFAULT_STALE_CANCEL_REQUESTED_TTL_MINUTES,
    ReapedCancelRequestedMissionInfo,
    reap_stale_cancel_requested_queued_vp_missions,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _insert_mission(
    conn: sqlite3.Connection,
    mission_id: str,
    *,
    vp_id: str = "vp.coder.primary",
    status: str = "queued",
    cancel_requested: int = 1,
    mission_type: str = "task",
    created_at: str | None = None,
    updated_at: str | None = None,
    completed_at: str | None = None,
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO vp_missions
          (mission_id, vp_id, status, mission_type, objective, priority_tier,
           cancel_requested, created_at, updated_at, completed_at)
        VALUES (?, ?, ?, ?, ?, 'background', ?, ?, ?, ?)
        """,
        (
            mission_id,
            vp_id,
            status,
            mission_type,
            "test objective",
            cancel_requested,
            created_at or now,
            updated_at or now,
            completed_at,
        ),
    )
    conn.commit()


def test_reaps_queued_cancel_requested_past_ttl() -> None:
    """The exact orphan shape: queued + cancel_requested=1, >TTL old. Flipped
    to cancelled with a backfilled completed_at and a vp_events audit row."""
    conn = _fresh_db()
    mid = "vp-mission-orphan-1"
    _insert_mission(conn, mid, updated_at=_ago(180))

    reaped = reap_stale_cancel_requested_queued_vp_missions(conn)

    assert len(reaped) == 1
    info = reaped[0]
    assert isinstance(info, ReapedCancelRequestedMissionInfo)
    assert info.mission_id == mid
    assert info.vp_id == "vp.coder.primary"
    assert info.ttl_minutes == DEFAULT_STALE_CANCEL_REQUESTED_TTL_MINUTES
    assert "cancel_requested_queued_stale" in info.terminal_reason

    row = conn.execute(
        "SELECT status, cancel_requested, completed_at FROM vp_missions WHERE mission_id = ?",
        (mid,),
    ).fetchone()
    assert row["status"] == "cancelled"
    assert row["cancel_requested"] == 1
    assert row["completed_at"] is not None  # terminal timestamp backfilled

    ev = conn.execute(
        "SELECT event_type, payload_json FROM vp_events WHERE mission_id = ?",
        (mid,),
    ).fetchone()
    assert ev["event_type"] == "vp.mission.cancelled"
    assert "vp_mission_reaper" in ev["payload_json"]


def test_ttl_guard_leaves_recent_cancel_request_alone() -> None:
    """Running-safety: a cancel requested seconds ago MAY still be
    mid-processing by the dispatcher loop — must not be reaped inside TTL."""
    conn = _fresh_db()
    mid = "vp-mission-fresh-cancel"
    _insert_mission(conn, mid, updated_at=_ago(0))

    assert reap_stale_cancel_requested_queued_vp_missions(conn) == []

    row = conn.execute(
        "SELECT status, cancel_requested FROM vp_missions WHERE mission_id = ?",
        (mid,),
    ).fetchone()
    assert row["status"] == "queued"  # untouched
    assert row["cancel_requested"] == 1


def test_explicit_small_ttl_reaps_recent_orphan() -> None:
    """Operator backfill path: an explicit small min-age reaps a young orphan
    the default 60m TTL would (correctly) leave behind."""
    conn = _fresh_db()
    mid = "vp-mission-young"
    _insert_mission(conn, mid, updated_at=_ago(5))

    # default TTL (60m) leaves a 5m-old orphan alone
    assert reap_stale_cancel_requested_queued_vp_missions(conn) == []
    # explicit 1m min-age reaps it
    reaped = reap_stale_cancel_requested_queued_vp_missions(conn, ttl_minutes=1)
    assert len(reaped) == 1
    assert reaped[0].ttl_minutes == 1
    assert (
        conn.execute(
            "SELECT status FROM vp_missions WHERE mission_id = ?", (mid,)
        ).fetchone()["status"]
        == "cancelled"
    )


def test_idempotent_second_call_is_noop() -> None:
    """Idempotency: a second pass finds nothing and writes no duplicate audit."""
    conn = _fresh_db()
    mid = "vp-mission-idem"
    _insert_mission(conn, mid, updated_at=_ago(120))

    assert reap_stale_cancel_requested_queued_vp_missions(conn)  # first pass finalizes
    assert reap_stale_cancel_requested_queued_vp_missions(conn) == []  # nothing left

    # exactly one audit row — not two
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM vp_events WHERE mission_id = ?", (mid,)
        ).fetchone()[0]
        == 1
    )


def test_does_not_touch_other_states() -> None:
    """Scope is queued+cancel_requested=1 ONLY. Fresh queued
    (cancel_requested=0), running, dispatched, completed, and already-cancelled
    missions are all left alone."""
    conn = _fresh_db()
    cases = [
        ("vp-mission-fresh-queued", "queued", 0, None),
        ("vp-mission-running", "running", 1, None),
        ("vp-mission-dispatched", "dispatched", 1, None),
        ("vp-mission-completed", "completed", 1, _ago(300)),
        ("vp-mission-already-cancelled", "cancelled", 1, _ago(300)),
    ]
    for mid, status, cr, completed_at in cases:
        _insert_mission(
            conn, mid, status=status, cancel_requested=cr,
            updated_at=_ago(300), completed_at=completed_at,
        )

    assert reap_stale_cancel_requested_queued_vp_missions(conn) == []

    for mid, expected_status, _, _ in cases:
        assert (
            conn.execute(
                "SELECT status FROM vp_missions WHERE mission_id = ?", (mid,)
            ).fetchone()["status"]
            == expected_status
        )


def test_vp_filter_scopes_reap() -> None:
    conn = _fresh_db()
    coder = "vp-mission-coder"
    general = "vp-mission-general"
    _insert_mission(conn, coder, vp_id="vp.coder.primary", updated_at=_ago(180))
    _insert_mission(conn, general, vp_id="vp.general.primary", updated_at=_ago(180))

    reaped = reap_stale_cancel_requested_queued_vp_missions(
        conn, vp_id="vp.coder.primary"
    )
    assert {r.mission_id for r in reaped} == {coder}
    assert (
        conn.execute(
            "SELECT status FROM vp_missions WHERE mission_id = ?", (general,)
        ).fetchone()["status"]
        == "queued"
    )


def test_dry_run_makes_no_mutation_but_previews() -> None:
    conn = _fresh_db()
    mid = "vp-mission-dry"
    _insert_mission(conn, mid, updated_at=_ago(180))

    reaped = reap_stale_cancel_requested_queued_vp_missions(conn, dry_run=True)
    assert len(reaped) == 1
    assert reaped[0].mission_id == mid

    row = conn.execute(
        "SELECT status, completed_at FROM vp_missions WHERE mission_id = ?",
        (mid,),
    ).fetchone()
    assert row["status"] == "queued"  # untouched
    assert row["completed_at"] is None
    assert conn.execute("SELECT COUNT(*) FROM vp_events").fetchone()[0] == 0


def test_check_stuck_vp_missions_self_heals_orphan(
    tmp_path, monkeypatch
) -> None:
    """End-to-end through the real health loop: the queued+cancel_requested=1
    orphan is self-healed by check_stuck_vp_missions and a finding surfaced."""
    from universal_agent.utils import db_health_monitor

    db_path = tmp_path / "vp_state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    mid = "vp-mission-e2e"
    conn.execute(
        """
        INSERT INTO vp_missions
          (mission_id, vp_id, status, mission_type, objective, priority_tier,
           cancel_requested, created_at, updated_at)
        VALUES (?, 'vp.coder.primary', 'queued', 'task', 'obj', 'background',
                1, ?, ?)
        """,
        (mid, _ago(180), _ago(180)),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("UA_VP_DB_PATH", str(db_path))
    findings = db_health_monitor.check_stuck_vp_missions()

    ids = {f.finding_id for f in findings}
    assert "stale_cancel_requested_queued_finalized" in ids

    verify = sqlite3.connect(str(db_path))
    verify.row_factory = sqlite3.Row
    row = verify.execute(
        "SELECT status, completed_at FROM vp_missions WHERE mission_id = ?",
        (mid,),
    ).fetchone()
    assert row["status"] == "cancelled"
    assert row["completed_at"] is not None
    ev = verify.execute(
        "SELECT event_type FROM vp_events WHERE mission_id = ?", (mid,)
    ).fetchone()
    assert ev["event_type"] == "vp.mission.cancelled"
    verify.close()


def test_default_ttl_matches_fallback_reaper_ttl() -> None:
    """The cancel-requested orphan TTL must equal the generic run fallback TTL
    so a stuck VP mission gets at least as much grace as any run_kind."""
    from universal_agent.services.stuck_run_reaper import DEFAULT_FALLBACK_TTL_MINUTES

    assert DEFAULT_STALE_CANCEL_REQUESTED_TTL_MINUTES == DEFAULT_FALLBACK_TTL_MINUTES
