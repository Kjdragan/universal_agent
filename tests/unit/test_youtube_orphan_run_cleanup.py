"""Regression: a youtube_tutorial_hook run orphaned run=queued / attempt=queued
(by a manual-webhook idle-timeout retry that never leased) must self-finalize.

A manual-webhook idle-timeout (``hook_idle_timeout_{N}s``) enqueues an automatic
retry via ``hooks_service._queue_or_finalize_youtube_attempt`` ->
``_schedule_youtube_retry_attempt`` (a fire-and-forget ``asyncio.create_task``).
If that dispatch coroutine never leases the new attempt — there is no live
session to resume — the run sits **queued** with its latest attempt **queued**
FOREVER. That state is:

  * invisible to ``finalize_orphaned_run_attempts`` (it requires the parent run
    to already be terminal — ``r.status NOT IN active``);
  * invisible to ``reap_stale_runs`` (it scopes to ``runs.status='running'``); and
  * counted permanently by the ``check_stale_runs`` "stuck in running/queued"
    alert — the exact live orphan ``run_youtube_tutorial_hook_85317f11d0df``
    (video ``roGmNXASYOM``) that held the alert open on 2026-07-10.

``finalize_stale_youtube_hook_runs`` reconciles it idempotently past a TTL,
finalizing BOTH the run and its latest attempt to ``failed`` /
``session_crashed`` WITHOUT surfacing a duplicate vp_failure card.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

from universal_agent.durable.migrations import ensure_schema
from universal_agent.services.stuck_run_reaper import (
    DEFAULT_YOUTUBE_ORPHAN_TTL_MINUTES,
    ReapedYouTubeOrphanInfo,
    finalize_stale_youtube_hook_runs,
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
    status: str = "queued",
    terminal_reason: str | None = None,
    run_kind: str = "youtube_tutorial_hook",
    latest_attempt_id: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    last_heartbeat_at: str | None = None,
    provider_session_id: str | None = None,
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO runs (
            run_id, created_at, updated_at, status, entrypoint, run_spec_json,
            run_kind, terminal_reason, latest_attempt_id, last_heartbeat_at,
            provider_session_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            created_at or now,
            updated_at or now,
            status,
            "hook",
            "{}",
            run_kind,
            terminal_reason,
            latest_attempt_id,
            last_heartbeat_at,
            provider_session_id,
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


def _alert_count(conn: sqlite3.Connection, cutoff: str | None = None) -> int:
    """The exact COUNT query ``check_stale_runs`` uses to trip the alert."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM runs r
        JOIN run_attempts a ON a.attempt_id = r.latest_attempt_id
        WHERE a.status IN ('running', 'queued', 'blocked')
          AND a.started_at < ?
        """,
        (cutoff or _ago(2),),
    ).fetchone()
    return int(row["cnt"])


def test_finalizes_queued_queued_youtube_orphan_past_ttl() -> None:
    """The exact live-orphan shape: queued run + queued latest attempt, >TTL old,
    no live session. Both run AND attempt finalize to failed/session_crashed."""
    conn = _fresh_db()
    run_id = "run_youtube_tutorial_hook_85317f11d0df"
    attempt_id = f"{run_id}:attempt:retry-2"
    _insert_run(
        conn,
        run_id,
        status="queued",
        latest_attempt_id=attempt_id,
        created_at=_ago(3),
        updated_at=_ago(3),
        provider_session_id="dead-session-1",
    )
    _insert_attempt(conn, attempt_id, run_id, status="queued", started_at=_ago(3))

    finalized = finalize_stale_youtube_hook_runs(conn)

    assert len(finalized) == 1
    info = finalized[0]
    assert isinstance(info, ReapedYouTubeOrphanInfo)
    assert info.run_id == run_id
    assert info.attempt_id == attempt_id
    assert info.run_status_before == "queued"
    assert info.attempt_status_before == "queued"
    assert "youtube_orphan_stale" in info.terminal_reason

    run = conn.execute(
        "SELECT status, terminal_reason FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert run["status"] == "failed"
    assert "youtube_orphan_stale" in run["terminal_reason"]

    attempt = conn.execute(
        "SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)
    ).fetchone()
    assert attempt["status"] == "failed"
    assert attempt["ended_at"] is not None
    assert attempt["lease_owner"] is None
    assert attempt["lease_expires_at"] is None
    assert attempt["failure_class"] == "session_crashed"
    assert "stale_youtube_orphan" in attempt["failure_reason"]


def test_alert_count_drops_to_zero_after_sweep() -> None:
    """The orphan trips the check_stale_runs count query before the sweep and
    clears it after — this is the count that held the alert open."""
    conn = _fresh_db()
    run_id = "run_orphan_alert"
    att = f"{run_id}:attempt:1"
    _insert_run(conn, run_id, latest_attempt_id=att, created_at=_ago(3), updated_at=_ago(3))
    _insert_attempt(conn, att, run_id, status="queued", started_at=_ago(3))

    assert _alert_count(conn) == 1  # orphan trips the alert before sweep
    finalize_stale_youtube_hook_runs(conn)
    assert _alert_count(conn) == 0  # reconciled — alert clears


def test_ttl_guard_leaves_legitimate_in_flight_run_alone() -> None:
    """A youtube run that is queued/queued but RECENT (inside the TTL) is a
    legitimately in-flight retry, not an orphan — must not be reaped."""
    conn = _fresh_db()
    run_id = "run_fresh_retry"
    att = f"{run_id}:attempt:2"
    _insert_run(conn, run_id, latest_attempt_id=att, created_at=_ago(0), updated_at=_ago(0))
    _insert_attempt(conn, att, run_id, status="queued", started_at=_ago(0))

    assert finalize_stale_youtube_hook_runs(conn) == []

    run = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    assert run["status"] == "queued"  # untouched


def test_does_not_touch_non_youtube_orphans_or_terminal_runs() -> None:
    """Scope is youtube_tutorial_hook + active/active only. A generic-hook
    queued/queued run, a youtube run that already completed, and a youtube run
    whose attempt is already terminal are all left alone (the generic orphan
    reaper / run's own finalization owns those)."""
    conn = _fresh_db()
    # generic-hook orphan — not our lane
    gen = "run_generic_hook"
    gen_att = f"{gen}:attempt:1"
    _insert_run(
        conn,
        gen,
        status="queued",
        run_kind="generic_hook",
        latest_attempt_id=gen_att,
        created_at=_ago(5),
        updated_at=_ago(5),
    )
    _insert_attempt(conn, gen_att, gen, status="queued", started_at=_ago(5))
    # youtube run already terminal with a terminal attempt
    done = "run_yt_done"
    done_att = f"{done}:attempt:1"
    _insert_run(
        conn,
        done,
        status="completed",
        terminal_reason="completed",
        latest_attempt_id=done_att,
        created_at=_ago(5),
        updated_at=_ago(5),
    )
    _insert_attempt(conn, done_att, done, status="completed", started_at=_ago(5))

    assert finalize_stale_youtube_hook_runs(conn) == []

    gen_row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (gen,)).fetchone()
    assert gen_row["status"] == "queued"  # untouched (not youtube)
    done_row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (done,)).fetchone()
    assert done_row["status"] == "completed"  # untouched (already terminal)


def test_idempotent_second_call_is_noop() -> None:
    conn = _fresh_db()
    run_id = "run_yt_idem"
    att = f"{run_id}:attempt:1"
    _insert_run(conn, run_id, latest_attempt_id=att, created_at=_ago(3), updated_at=_ago(3))
    _insert_attempt(conn, att, run_id, status="queued", started_at=_ago(3))

    assert finalize_stale_youtube_hook_runs(conn)  # first pass finalizes
    assert finalize_stale_youtube_hook_runs(conn) == []  # second pass: nothing left


def test_no_duplicate_vp_failure_card_surfaced(monkeypatch) -> None:
    """The sweep must NOT emit a vp_failure / surface card — the run never
    reached an outcome worth re-surfacing. Guards the 'no duplicate
    failure-surfacing' contract shared with finalize_orphaned_run_attempts."""
    from universal_agent.services import vp_failure_rescue

    def _must_not_surface(**kwargs):  # noqa: ANN002,ANN003
        raise AssertionError(
            "finalize_stale_youtube_hook_runs must not surface a vp_failure card; "
            f"got call with {kwargs}"
        )

    monkeypatch.setattr(
        vp_failure_rescue, "surface_failure_to_simone", _must_not_surface
    )

    conn = _fresh_db()
    run_id = "run_yt_nosurface"
    att = f"{run_id}:attempt:1"
    _insert_run(conn, run_id, latest_attempt_id=att, created_at=_ago(3), updated_at=_ago(3))
    _insert_attempt(conn, att, run_id, status="queued", started_at=_ago(3))

    finalized = finalize_stale_youtube_hook_runs(conn)  # raises if it surfaced
    assert len(finalized) == 1


def test_check_stale_runs_self_heals_youtube_orphan_via_real_loop(
    tmp_path, monkeypatch
) -> None:
    """End-to-end through the real health loop: the queued/queued youtube orphan
    is self-healed BEFORE the count query runs, so 'stale_runs_detected' never
    fires and a 'stale_youtube_orphans_finalized' finding is emitted instead."""
    from universal_agent.utils import db_health_monitor

    db_path = tmp_path / "runtime_state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    run_id = "run_yt_e2e"
    att = f"{run_id}:attempt:2"
    conn.execute(
        """
        INSERT INTO runs (run_id, created_at, updated_at, status, entrypoint,
                          run_spec_json, run_kind, terminal_reason,
                          latest_attempt_id)
        VALUES (?, ?, ?, 'queued', 'hook', '{}', 'youtube_tutorial_hook',
                NULL, ?)
        """,
        (run_id, _ago(3), _ago(3), att),
    )
    conn.execute(
        """
        INSERT INTO run_attempts (attempt_id, run_id, attempt_number, created_at,
                                  updated_at, status, started_at)
        VALUES (?, ?, 2, ?, ?, 'queued', ?)
        """,
        (att, run_id, _ago(3), _ago(3), _ago(3)),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(db_path))
    findings = db_health_monitor.check_stale_runs()

    ids = {f.finding_id for f in findings}
    assert "stale_runs_detected" not in ids  # alert did NOT fire — self-healed
    assert "stale_youtube_orphans_finalized" in ids  # sweep surfaced a finding

    verify = sqlite3.connect(str(db_path))
    verify.row_factory = sqlite3.Row
    run = verify.execute(
        "SELECT status, terminal_reason FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert run["status"] == "failed"
    assert "youtube_orphan_stale" in run["terminal_reason"]
    attempt = verify.execute(
        "SELECT status, failure_class FROM run_attempts WHERE attempt_id = ?", (att,)
    ).fetchone()
    assert attempt["status"] == "failed"
    assert attempt["failure_class"] == "session_crashed"
    verify.close()


def test_default_ttl_matches_fallback_reaper_ttl() -> None:
    """The youtube-orphan TTL must equal the generic fallback reaper TTL so a
    legitimately in-flight youtube run gets at least as much grace as any other
    run_kind in reap_stale_runs."""
    from universal_agent.services.stuck_run_reaper import DEFAULT_FALLBACK_TTL_MINUTES

    assert DEFAULT_YOUTUBE_ORPHAN_TTL_MINUTES == DEFAULT_FALLBACK_TTL_MINUTES
