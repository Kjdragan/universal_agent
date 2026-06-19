"""Regression: an orphaned run_attempt on a terminal run must self-finalize.

A failure-finalization path can mark ``runs.status`` terminal (e.g. ``failed``
with ``terminal_reason='hook_dispatch_failed'``) without finalizing the linked
``run_attempts`` row, leaving it parked in ``running``/``queued``/``blocked``.
That residual attempt is invisible to ``reap_stale_runs`` (which scopes to
``runs.status='running'`` AND ``run_attempts.status='running'``) and trips the
``check_stale_runs`` "stuck in running/queued >2.0h" alert permanently — this
is exactly the live orphan ``run_youtube_tutorial_hook_d9bb9c80c799`` (failed
run, queued attempt, ~33h old) that held the alert open.

``finalize_orphaned_run_attempts`` reconciles it idempotently WITHOUT emitting
a duplicate failure-surface card.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

from universal_agent.durable.migrations import ensure_schema
from universal_agent.services.stuck_run_reaper import (
    OrphanedAttemptInfo,
    finalize_orphaned_run_attempts,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _insert_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: str = "failed",
    terminal_reason: str = "hook_dispatch_failed",
    run_kind: str = "youtube_tutorial_hook",
    latest_attempt_id: str | None = None,
    created_at: str | None = None,
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO runs (
            run_id, created_at, updated_at, status, entrypoint, run_spec_json,
            run_kind, terminal_reason, latest_attempt_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            created_at or now,
            now,
            status,
            "hook",
            "{}",
            run_kind,
            terminal_reason,
            latest_attempt_id,
        ),
    )
    conn.commit()


def _insert_attempt(
    conn: sqlite3.Connection,
    attempt_id: str,
    run_id: str,
    *,
    status: str = "queued",
    started_at: str | None = None,
    lease_owner: str | None = None,
    lease_expires_at: str | None = None,
    attempt_number: int = 1,
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO run_attempts (
            attempt_id, run_id, attempt_number, created_at, updated_at, status,
            lease_owner, lease_expires_at, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            attempt_id,
            run_id,
            attempt_number,
            now,
            now,
            status,
            lease_owner,
            lease_expires_at,
            started_at or now,
        ),
    )
    conn.commit()


def test_finalizes_orphaned_queued_attempt_on_failed_run() -> None:
    """The exact live-orphan shape: failed run + queued latest attempt."""
    conn = _fresh_db()
    run_id = "run_youtube_tutorial_hook_d9bb9c80c799"
    attempt_id = f"{run_id}:attempt:b5877a7f-821c-4f02-b26c-8b03f071142e"
    _insert_run(
        conn,
        run_id,
        status="failed",
        terminal_reason="hook_dispatch_failed",
        latest_attempt_id=attempt_id,
        created_at=_ago(33),
    )
    _insert_attempt(conn, attempt_id, run_id, status="queued", started_at=_ago(33))

    finalized = finalize_orphaned_run_attempts(conn)

    assert len(finalized) == 1
    info = finalized[0]
    assert isinstance(info, OrphanedAttemptInfo)
    assert info.attempt_id == attempt_id
    assert info.run_status == "failed"
    assert info.attempt_status_before == "queued"
    assert info.attempt_status_after == "failed"
    assert info.terminal_reason == "hook_dispatch_failed"

    row = conn.execute(
        "SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)
    ).fetchone()
    assert row["status"] == "failed"
    assert row["ended_at"] is not None
    assert row["lease_owner"] is None
    assert row["lease_expires_at"] is None
    assert row["failure_class"] == "orphaned_attempt_cleanup"
    assert "orphaned_attempt_cleanup" in row["failure_reason"]
    assert "hook_dispatch_failed" in row["failure_reason"]


def test_idempotent_second_call_is_noop() -> None:
    conn = _fresh_db()
    run_id = "run_x"
    att = f"{run_id}:attempt:1"
    _insert_run(conn, run_id, latest_attempt_id=att)
    _insert_attempt(conn, att, run_id, status="running")

    assert finalize_orphaned_run_attempts(conn)  # first pass reconciles

    assert finalize_orphaned_run_attempts(conn) == []  # second pass: nothing left


def test_does_not_touch_legitimate_in_progress_or_already_terminal_runs() -> None:
    conn = _fresh_db()
    # genuinely-running run with a running, leased attempt — must be left alone
    live_run = "run_live"
    live_att = f"{live_run}:attempt:1"
    _insert_run(
        conn,
        live_run,
        status="running",
        terminal_reason=None,
        latest_attempt_id=live_att,
    )
    _insert_attempt(
        conn,
        live_att,
        live_run,
        status="running",
        lease_owner="worker-1",
        lease_expires_at=_ago(-1),
    )
    # completed run whose attempt is already terminal — also untouched
    done_run = "run_done"
    done_att = f"{done_run}:attempt:1"
    _insert_run(
        conn,
        done_run,
        status="completed",
        terminal_reason="completed",
        latest_attempt_id=done_att,
    )
    _insert_attempt(conn, done_att, done_run, status="completed")

    assert finalize_orphaned_run_attempts(conn) == []

    live = conn.execute(
        "SELECT status, lease_owner FROM run_attempts WHERE attempt_id = ?",
        (live_att,),
    ).fetchone()
    assert live["status"] == "running"
    assert live["lease_owner"] == "worker-1"


def test_no_duplicate_vp_failure_card_surfaced(monkeypatch) -> None:
    """The orphan cleanup must NOT emit a vp_failure / surface card — the run
    already recorded its terminal outcome. Guards the 'no duplicate
    failure-surfacing' contract against future refactors that might re-add a
    surfacing call here."""
    from universal_agent.services import vp_failure_rescue

    def _must_not_surface(**kwargs):  # noqa: ANN002,ANN003
        raise AssertionError(
            "finalize_orphaned_run_attempts must not surface a vp_failure card; "
            f"got call with {kwargs}"
        )

    monkeypatch.setattr(
        vp_failure_rescue, "surface_failure_to_simone", _must_not_surface
    )

    conn = _fresh_db()
    run_id = "run_orphan"
    att = f"{run_id}:attempt:1"
    _insert_run(conn, run_id, latest_attempt_id=att)
    _insert_attempt(conn, att, run_id, status="queued")

    finalized = finalize_orphaned_run_attempts(conn)  # raises if it surfaced
    assert len(finalized) == 1


def test_alert_count_query_drops_to_zero_after_cleanup() -> None:
    """The exact COUNT query ``check_stale_runs`` uses must return 0 once the
    orphan is reconciled — this is the count that held the alert open."""
    conn = _fresh_db()
    run_id = "run_orphan_alert"
    att = f"{run_id}:attempt:1"
    _insert_run(conn, run_id, latest_attempt_id=att)
    _insert_attempt(conn, att, run_id, status="queued", started_at=_ago(33))

    cutoff = _ago(2)

    def _alert_count() -> int:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM runs r
            JOIN run_attempts a ON a.attempt_id = r.latest_attempt_id
            WHERE a.status IN ('running', 'queued', 'blocked')
              AND a.started_at < ?
            """,
            (cutoff,),
        ).fetchone()
        return int(row["cnt"])

    assert _alert_count() == 1  # orphan trips the alert before cleanup
    finalize_orphaned_run_attempts(conn)
    assert _alert_count() == 0  # reconciled — alert clears


def test_check_stale_runs_self_heals_orphan_via_real_loop(
    tmp_path, monkeypatch
) -> None:
    """End-to-end through the real health loop: the orphan is self-healed BEFORE
    the count query runs, so 'stale_runs_detected' never fires and an info
    'orphaned_attempts_finalized' finding is emitted instead."""
    from universal_agent.utils import db_health_monitor

    db_path = tmp_path / "runtime_state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    run_id = "run_e2e"
    att = f"{run_id}:attempt:1"
    conn.execute(
        """
        INSERT INTO runs (run_id, created_at, updated_at, status, entrypoint,
                          run_spec_json, run_kind, terminal_reason,
                          latest_attempt_id)
        VALUES (?, ?, ?, 'failed', 'hook', '{}', 'youtube_tutorial_hook',
                'hook_dispatch_failed', ?)
        """,
        (run_id, _ago(33), _now(), att),
    )
    conn.execute(
        """
        INSERT INTO run_attempts (attempt_id, run_id, attempt_number, created_at,
                                  updated_at, status, started_at)
        VALUES (?, ?, 1, ?, ?, 'queued', ?)
        """,
        (att, run_id, _ago(33), _now(), _ago(33)),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(db_path))
    findings = db_health_monitor.check_stale_runs()

    ids = {f.finding_id for f in findings}
    assert "stale_runs_detected" not in ids  # alert did NOT fire — self-healed
    assert "orphaned_attempts_finalized" in ids  # cleanup surfaced an info finding

    # and the DB row is actually finalized
    verify = sqlite3.connect(str(db_path))
    verify.row_factory = sqlite3.Row
    row = verify.execute(
        "SELECT status, failure_class FROM run_attempts WHERE attempt_id = ?", (att,)
    ).fetchone()
    assert row["status"] == "failed"
    assert row["failure_class"] == "orphaned_attempt_cleanup"
    verify.close()
