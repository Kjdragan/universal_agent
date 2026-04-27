"""Tests for the stuck-run reaper — progress-based TTL reaping.

The reaper uses COALESCE(last_heartbeat_at, updated_at) as the "last progress"
timestamp. A run is reaped when it has not made progress for longer than its
TTL, NOT based on absolute run age.

Design principle: A run that's been actively heartbeating for 2 hours is fine.
A run that stopped heartbeating 30 minutes ago is stuck and gets reaped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from universal_agent.durable.migrations import ensure_schema

# These will be the module under test — tests are RED until we create the module.
from universal_agent.services.stuck_run_reaper import (
    ReapedRunInfo,
    reap_stale_runs,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ago(minutes: int) -> str:
    """Return ISO timestamp for N minutes ago."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


@pytest.fixture
def db():
    """Create an in-memory runtime_state DB with schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    yield conn
    conn.close()


def _insert_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: str = "running",
    run_kind: str = "todo_execution",
    created_at: str | None = None,
    updated_at: str | None = None,
    last_heartbeat_at: str | None = None,
) -> None:
    """Helper to insert a test run."""
    now = _now()
    conn.execute(
        """
        INSERT INTO runs (
            run_id, created_at, updated_at, status, entrypoint, run_spec_json,
            run_kind, last_heartbeat_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            created_at or now,
            updated_at or now,
            status,
            "task_hub",
            "{}",
            run_kind,
            last_heartbeat_at,
        ),
    )
    conn.commit()


# ── Core Reaping Logic ───────────────────────────────────────────────────────


class TestReapStaleRuns:
    """Test progress-based reaping."""

    def test_reaps_run_with_stale_heartbeat(self, db):
        """A run whose last_heartbeat_at is 40 minutes ago should be reaped
        (default todo TTL = 30 min)."""
        _insert_run(
            db,
            "run_stale_hb",
            last_heartbeat_at=_ago(40),
            updated_at=_ago(40),
        )
        reaped = reap_stale_runs(db)

        assert len(reaped) == 1
        assert reaped[0].run_id == "run_stale_hb"
        assert reaped[0].reason == "no_progress"

        # Verify DB state
        row = db.execute(
            "SELECT status, terminal_reason FROM runs WHERE run_id = 'run_stale_hb'"
        ).fetchone()
        assert row["status"] == "timed_out"
        assert "reaper" in row["terminal_reason"]

    def test_does_not_reap_actively_heartbeating_run(self, db):
        """A run that heartbeated 5 minutes ago is making progress — leave it alone,
        even if it was created 3 hours ago."""
        _insert_run(
            db,
            "run_active",
            created_at=_ago(180),  # Started 3 hours ago
            updated_at=_ago(5),    # But updated 5 min ago
            last_heartbeat_at=_ago(5),  # And heartbeated 5 min ago
        )
        reaped = reap_stale_runs(db)
        assert len(reaped) == 0

        # Verify still running
        row = db.execute(
            "SELECT status FROM runs WHERE run_id = 'run_active'"
        ).fetchone()
        assert row["status"] == "running"

    def test_uses_updated_at_when_no_heartbeat(self, db):
        """If last_heartbeat_at is NULL, fall back to updated_at as progress signal."""
        _insert_run(
            db,
            "run_no_hb",
            last_heartbeat_at=None,
            updated_at=_ago(45),
        )
        reaped = reap_stale_runs(db)

        assert len(reaped) == 1
        assert reaped[0].run_id == "run_no_hb"

    def test_does_not_reap_completed_runs(self, db):
        """Terminal statuses (completed, failed, timed_out) are never touched."""
        _insert_run(db, "run_done", status="completed", updated_at=_ago(120))
        _insert_run(db, "run_failed", status="failed", updated_at=_ago(120))
        _insert_run(db, "run_timed", status="timed_out", updated_at=_ago(120))

        reaped = reap_stale_runs(db)
        assert len(reaped) == 0

    def test_cron_dispatch_uses_longer_ttl(self, db):
        """cron_job_dispatch runs get 60-minute TTL, not 30."""
        # 40 minutes stale — within cron TTL, outside todo TTL
        _insert_run(
            db,
            "run_cron",
            run_kind="cron_job_dispatch",
            updated_at=_ago(40),
            last_heartbeat_at=_ago(40),
        )
        _insert_run(
            db,
            "run_todo",
            run_kind="todo_execution",
            updated_at=_ago(40),
            last_heartbeat_at=_ago(40),
        )
        reaped = reap_stale_runs(db)

        reaped_ids = [r.run_id for r in reaped]
        assert "run_todo" in reaped_ids  # 40 min > 30 min todo TTL
        assert "run_cron" not in reaped_ids  # 40 min < 60 min cron TTL

    def test_cron_dispatch_reaped_after_its_ttl(self, db):
        """A cron_job_dispatch run IS reaped when stale beyond 60 min."""
        _insert_run(
            db,
            "run_cron_stale",
            run_kind="cron_job_dispatch",
            updated_at=_ago(70),
            last_heartbeat_at=_ago(70),
        )
        reaped = reap_stale_runs(db)
        assert len(reaped) == 1
        assert reaped[0].run_id == "run_cron_stale"

    def test_returns_structured_reap_info(self, db):
        """Reaped results include run_id, run_kind, stale_minutes, and reason."""
        _insert_run(
            db,
            "run_info_test",
            run_kind="todo_execution",
            updated_at=_ago(60),
            last_heartbeat_at=_ago(60),
        )
        reaped = reap_stale_runs(db)

        assert len(reaped) == 1
        info = reaped[0]
        assert info.run_id == "run_info_test"
        assert info.run_kind == "todo_execution"
        assert info.stale_minutes >= 55  # Allow small clock skew
        assert info.reason == "no_progress"

    def test_custom_ttl_overrides(self, db):
        """Custom TTL values override defaults."""
        _insert_run(
            db,
            "run_custom",
            run_kind="todo_execution",
            updated_at=_ago(15),
            last_heartbeat_at=_ago(15),
        )
        # Default 30-min TTL would NOT reap this, but custom 10-min will
        reaped = reap_stale_runs(db, todo_ttl_minutes=10)
        assert len(reaped) == 1

        # Reset for inverse test
        db.execute("UPDATE runs SET status='running', terminal_reason=NULL WHERE run_id='run_custom'")
        db.commit()

        # Custom 20-min TTL should NOT reap 15-min-stale run
        reaped = reap_stale_runs(db, todo_ttl_minutes=20)
        assert len(reaped) == 0

    def test_empty_db_returns_empty(self, db):
        """No runs at all → empty result, no errors."""
        reaped = reap_stale_runs(db)
        assert reaped == []

    def test_multiple_stale_runs_all_reaped(self, db):
        """Multiple stuck runs are all reaped in one call."""
        for i in range(5):
            _insert_run(
                db,
                f"run_batch_{i}",
                updated_at=_ago(60),
                last_heartbeat_at=_ago(60),
            )
        reaped = reap_stale_runs(db)
        assert len(reaped) == 5

    def test_queued_status_not_reaped(self, db):
        """Queued runs are not yet executing — don't reap them."""
        _insert_run(db, "run_queued", status="queued", updated_at=_ago(120))
        reaped = reap_stale_runs(db)
        assert len(reaped) == 0


# ── Notification Content ─────────────────────────────────────────────────────


class TestReaperNotifications:
    """Verify the reaper produces actionable information for Simone."""

    def test_reap_info_has_notification_message(self, db):
        """Each reaped run should include a human-readable notification message."""
        _insert_run(
            db,
            "run_notify",
            run_kind="todo_execution",
            updated_at=_ago(45),
            last_heartbeat_at=_ago(45),
        )
        reaped = reap_stale_runs(db)
        assert len(reaped) == 1
        # The notification_message should mention the run_id and stale duration
        msg = reaped[0].notification_message
        assert "run_notify" in msg
        assert "minute" in msg.lower()
