"""Tests for the cron_consecutive_failures invariant.

Sibling to test_cron_staleness. Motivated by the 2026-05-23
paper_to_podcast incident: a cron failed 6 nights in a row without
firing any alarm because last_outcome wasn't propagating reliably.
This invariant reads task_hub_assignments directly so the streak is
caught regardless of last_outcome plumbing.
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest

from universal_agent.services import pipeline_invariants as pi
from universal_agent.services.pipeline_invariants import (
    clear_registry_for_tests,
    run_invariants,
)


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
    base_iso: str = "2026-05-",
) -> None:
    """Insert one assignment per state. ``states[0]`` is OLDEST,
    ``states[-1]`` is the most recent. started_at increments by 1 day so
    ORDER BY started_at DESC returns newest-first."""
    for idx, state in enumerate(states):
        day = 10 + idx
        conn.execute(
            "INSERT INTO task_hub_assignments "
            "(assignment_id, task_id, agent_id, state, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                f"{task_id}-{idx}",
                task_id,
                "cron_scheduler",
                state,
                f"{base_iso}{day:02d}T02:00:00Z",
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
