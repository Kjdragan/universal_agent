"""Tests for the cron_consecutive_failures invariant.

Sibling to test_cron_staleness. Motivated by the 2026-05-23
paper_to_podcast incident: a cron failed 6 nights in a row without
firing any alarm because last_outcome wasn't propagating reliably.
This invariant reads task_hub_assignments directly so the streak is
caught regardless of last_outcome plumbing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import sqlite3

import pytest

from universal_agent.services import pipeline_invariants as pi
from universal_agent.services.pipeline_invariants import (
    clear_registry_for_tests,
    run_invariants,
)

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _fresh_registry():
    clear_registry_for_tests()
    from universal_agent.services.invariants import cron_consecutive_failures
    importlib.reload(cron_consecutive_failures)
    yield
    clear_registry_for_tests()


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(
        """
        CREATE TABLE task_hub_assignments (
            assignment_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL DEFAULT 'cron_scheduler',
            state TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            result_summary TEXT
        )
        """
    )
    c.commit()
    return c


def _insert(
    conn: sqlite3.Connection,
    task_id: str,
    states: list[str],
    *,
    newest: datetime | None = None,
) -> None:
    """Insert one assignment per state. ``states[0]`` is OLDEST,
    ``states[-1]`` is the most recent. started_at steps back 1 day per
    older entry so ORDER BY started_at DESC returns newest-first.

    Timestamps are anchored near *now* by default (newest ≈ 1h ago) so the
    invariant's recency backstop treats the streak as live. Pass ``newest``
    to anchor the head of the history at a specific time (e.g. 40 days ago
    to exercise the abandoned-orphan backstop)."""
    head = newest if newest is not None else (datetime.now(UTC) - timedelta(hours=1))
    n = len(states)
    for idx, state in enumerate(states):
        # idx == n-1 is newest (== head); each older entry is 1 day earlier.
        started_at = head - timedelta(days=(n - 1 - idx))
        conn.execute(
            "INSERT INTO task_hub_assignments "
            "(assignment_id, task_id, agent_id, state, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                f"{task_id}-{idx}",
                task_id,
                "cron_scheduler",
                state,
                started_at.isoformat(),
            ),
        )
    conn.commit()


def test_registers_on_import() -> None:
    ids = {inv.id for inv in pi.get_registered_invariants()}
    assert "cron_consecutive_failures" in ids


def test_no_assignments_emits_nothing(conn: sqlite3.Connection) -> None:
    findings = run_invariants({"activity_conn": conn})
    assert all(f.metric_key != "cron_consecutive_failures" for f in findings)


def test_all_recent_success_emits_nothing(conn: sqlite3.Connection) -> None:
    _insert(conn, "cron:happy_job", ["completed", "completed", "completed", "completed"])
    findings = run_invariants({"activity_conn": conn})
    assert all(f.metric_key != "cron_consecutive_failures" for f in findings)


def test_two_consecutive_failures_below_threshold(conn: sqlite3.Connection) -> None:
    # Streak of 2 — under the threshold of 3, no finding.
    _insert(conn, "cron:flaky", ["completed", "completed", "failed", "failed"])
    findings = run_invariants({"activity_conn": conn})
    assert all(f.metric_key != "cron_consecutive_failures" for f in findings)


def test_three_consecutive_failures_fires(conn: sqlite3.Connection) -> None:
    # Three failures in a row (the head of the history) — should fire.
    _insert(
        conn,
        "cron:paper_to_podcast_daily",
        ["completed", "failed", "failed", "failed"],
    )
    findings = run_invariants({"activity_conn": conn})
    matched = [f for f in findings if f.metric_key == "cron_consecutive_failures"]
    assert len(matched) == 1
    streaks = matched[0].observed_value["streaks"]
    assert len(streaks) == 1
    assert streaks[0]["task_id"] == "cron:paper_to_podcast_daily"
    assert streaks[0]["streak"] == 3


def test_success_in_middle_resets_streak(conn: sqlite3.Connection) -> None:
    # Streak count walks newest-first and stops at the first success.
    # Pattern (oldest → newest): F F C F F — only 2 failures at head.
    _insert(conn, "cron:reset_check", ["failed", "failed", "completed", "failed", "failed"])
    findings = run_invariants({"activity_conn": conn})
    assert all(f.metric_key != "cron_consecutive_failures" for f in findings)


def test_mixed_failure_states_all_count(conn: sqlite3.Connection) -> None:
    # Any non-success state (cancelled, timeout_killed, etc.) counts
    # toward the streak — this is the actual paper_to_podcast signature.
    _insert(
        conn,
        "cron:multi_mode_fail",
        ["completed", "cancelled", "timeout_killed", "failed"],
    )
    findings = run_invariants({"activity_conn": conn})
    matched = [f for f in findings if f.metric_key == "cron_consecutive_failures"]
    assert len(matched) == 1
    assert matched[0].observed_value["streaks"][0]["streak"] == 3


def test_multiple_failing_crons_one_finding(conn: sqlite3.Connection) -> None:
    _insert(conn, "cron:job_a", ["failed", "failed", "failed"])
    _insert(conn, "cron:job_b", ["failed", "failed", "failed", "failed"])
    _insert(conn, "cron:job_c_healthy", ["completed", "completed"])
    findings = run_invariants({"activity_conn": conn})
    matched = [f for f in findings if f.metric_key == "cron_consecutive_failures"]
    assert len(matched) == 1
    streaks = matched[0].observed_value["streaks"]
    task_ids = {s["task_id"] for s in streaks}
    assert task_ids == {"cron:job_a", "cron:job_b"}
    # Sorted by streak descending — job_b's longer streak comes first.
    assert streaks[0]["task_id"] == "cron:job_b"
    assert streaks[0]["streak"] == 4


def test_non_cron_tasks_ignored(conn: sqlite3.Connection) -> None:
    # Only task_id LIKE 'cron:%' is in scope.
    _insert(conn, "email:abc123", ["failed", "failed", "failed"])
    _insert(conn, "tutorial-build:xyz", ["failed", "failed", "failed"])
    findings = run_invariants({"activity_conn": conn})
    assert all(f.metric_key != "cron_consecutive_failures" for f in findings)


def test_missing_activity_conn_no_crash() -> None:
    findings = run_invariants({})
    assert all(f.metric_key != "cron_consecutive_failures" for f in findings)


# --- Disabled / deleted-cron suppression (the claude_code_intel_sync fix) ---
# A cron disabled or deleted from the registry stops appending runs, so its
# leading-failure count freezes and would otherwise sit on the board forever.
# When cron metadata IS available in the context, only enabled & registered
# crons may raise a streak finding.


def test_disabled_cron_streak_suppressed_with_metadata(conn: sqlite3.Connection) -> None:
    # The live signature: a disabled cron with a long recent failure streak.
    _insert(conn, "cron:claude_code_intel_sync", ["failed"] * 6)
    cron_jobs = [
        {"job_id": "claude_code_intel_sync", "enabled": False, "cron_expr": "0 8,16,22 * * *"},
    ]
    findings = run_invariants({"activity_conn": conn, "cron_jobs": cron_jobs})
    assert all(f.metric_key != "cron_consecutive_failures" for f in findings)


def test_enabled_cron_streak_still_fires_with_metadata(conn: sqlite3.Connection) -> None:
    # Regression guard: the metadata filter must NOT over-suppress a live,
    # enabled cron that really is failing every run.
    _insert(conn, "cron:live_job", ["completed", "failed", "failed", "failed"])
    cron_jobs = [{"job_id": "live_job", "enabled": True, "cron_expr": "0 * * * *"}]
    findings = run_invariants({"activity_conn": conn, "cron_jobs": cron_jobs})
    matched = [f for f in findings if f.metric_key == "cron_consecutive_failures"]
    assert len(matched) == 1
    assert matched[0].observed_value["streaks"][0]["task_id"] == "cron:live_job"


def test_deleted_cron_streak_suppressed_with_metadata(conn: sqlite3.Connection) -> None:
    # cron:ghost has lingering assignments but is no longer registered — an
    # unknown job can't be "firing on schedule but failing".
    _insert(conn, "cron:ghost", ["failed", "failed", "failed", "failed"])
    cron_jobs = [{"job_id": "other_live", "enabled": True, "cron_expr": "0 * * * *"}]
    findings = run_invariants({"activity_conn": conn, "cron_jobs": cron_jobs})
    assert all(f.metric_key != "cron_consecutive_failures" for f in findings)


def test_disabled_and_enabled_mixed_only_enabled_fires(conn: sqlite3.Connection) -> None:
    # The exact production scenario: a disabled cron with a frozen streak
    # alongside a healthy enabled cron → no card at all.
    _insert(conn, "cron:claude_code_intel_sync", ["failed"] * 6)
    _insert(conn, "cron:healthy", ["completed", "completed", "completed"])
    cron_jobs = [
        {"job_id": "claude_code_intel_sync", "enabled": False, "cron_expr": "0 8,16,22 * * *"},
        {"job_id": "healthy", "enabled": True, "cron_expr": "0 * * * *"},
    ]
    findings = run_invariants({"activity_conn": conn, "cron_jobs": cron_jobs})
    assert all(f.metric_key != "cron_consecutive_failures" for f in findings)


def test_no_metadata_recent_streak_still_fires(conn: sqlite3.Connection) -> None:
    # Degraded path (no cron_jobs in ctx): a recent streak must still surface
    # so the invariant doesn't go dark when metadata is briefly unavailable.
    _insert(conn, "cron:live_failing", ["failed", "failed", "failed", "failed"])
    findings = run_invariants({"activity_conn": conn})
    matched = [f for f in findings if f.metric_key == "cron_consecutive_failures"]
    assert len(matched) == 1


def test_no_metadata_ancient_streak_suppressed_by_recency(conn: sqlite3.Connection) -> None:
    # Degraded path backstop: an abandoned task whose most recent run is far
    # older than the recency cutoff must NOT raise a frozen streak.
    _insert(
        conn,
        "cron:abandoned",
        ["failed", "failed", "failed", "failed"],
        newest=datetime.now(UTC) - timedelta(days=40),
    )
    findings = run_invariants({"activity_conn": conn})
    assert all(f.metric_key != "cron_consecutive_failures" for f in findings)
