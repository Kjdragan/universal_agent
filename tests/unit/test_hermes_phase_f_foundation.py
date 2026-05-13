"""Hermes Phase F.1 + F.2 foundation unit tests.

Foundation = schema columns + classifier + helper functions. The actual
spawn-site wiring (cron / VP CLI / demo workspace) lands in follow-up
PRs once the operator validates this foundation.

Coverage:

* F.1 — ``task_hub_assignments.worker_pid`` column round-trips.
* F.1 — ``record_worker_pid`` updates exactly the matching row.
* F.1 — ``classify_worker_exit`` handles all five outcome buckets.
* F.1 — ``PROTOCOL_VIOLATION_REASONS`` exposes the three site-specific
  reason strings F.3 will consume.
* F.2 — ``task_hub_items.max_runtime_seconds`` column round-trips.
* F.2 — ``resolve_max_runtime_seconds`` resolution order:
  task → env → 7200 default.
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services.worker_exit_classifier import (
    PROTOCOL_VIOLATION_REASONS,
    classify_worker_exit,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    task_hub.ensure_schema(c)
    yield c
    c.close()


def _seed_assignment(
    conn: sqlite3.Connection, *, assignment_id: str, task_id: str = "task:f"
) -> None:
    conn.execute(
        """
        INSERT INTO task_hub_assignments
            (assignment_id, task_id, agent_id, state, started_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        """,
        (assignment_id, task_id, "agent:x", "seized"),
    )
    conn.commit()


# ── F.1: worker_pid column ─────────────────────────────────────────────────


def test_worker_pid_column_exists_on_task_hub_assignments(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(task_hub_assignments)")}
    assert "worker_pid" in cols


def test_worker_pid_defaults_to_null(conn: sqlite3.Connection) -> None:
    _seed_assignment(conn, assignment_id="asg-1")
    row = conn.execute(
        "SELECT worker_pid FROM task_hub_assignments WHERE assignment_id = ?",
        ("asg-1",),
    ).fetchone()
    assert row is not None
    assert row["worker_pid"] is None


def test_record_worker_pid_persists_value(conn: sqlite3.Connection) -> None:
    _seed_assignment(conn, assignment_id="asg-2")
    updated = task_hub.record_worker_pid(conn, assignment_id="asg-2", worker_pid=12345)
    assert updated == 1
    row = conn.execute(
        "SELECT worker_pid FROM task_hub_assignments WHERE assignment_id = ?",
        ("asg-2",),
    ).fetchone()
    assert row is not None
    assert row["worker_pid"] == 12345


def test_record_worker_pid_zero_or_negative_ignored(conn: sqlite3.Connection) -> None:
    _seed_assignment(conn, assignment_id="asg-3")
    assert task_hub.record_worker_pid(conn, assignment_id="asg-3", worker_pid=0) == 0
    assert task_hub.record_worker_pid(conn, assignment_id="asg-3", worker_pid=-1) == 0


def test_record_worker_pid_empty_assignment_id_ignored(conn: sqlite3.Connection) -> None:
    assert task_hub.record_worker_pid(conn, assignment_id="", worker_pid=99) == 0
    assert task_hub.record_worker_pid(conn, assignment_id="   ", worker_pid=99) == 0


def test_record_worker_pid_unknown_assignment_returns_zero(conn: sqlite3.Connection) -> None:
    """Updating a non-existent assignment is a no-op (no exception)."""
    assert (
        task_hub.record_worker_pid(conn, assignment_id="asg-does-not-exist", worker_pid=7777)
        == 0
    )


# ── F.1: classify_worker_exit ───────────────────────────────────────────────


def test_classify_clean_exit_zero_task_closed() -> None:
    result = classify_worker_exit(return_code=0, task_closed_normally=True)
    assert result.outcome == "clean_exit_zero"
    assert result.is_protocol_violation is False
    assert result.is_failure is False


def test_classify_clean_exit_zero_no_disposition_is_protocol_violation() -> None:
    """The core F.3 trigger: rc=0 but task is still in_progress."""
    result = classify_worker_exit(return_code=0, task_closed_normally=False)
    assert result.outcome == "clean_exit_zero_no_disposition"
    assert result.is_protocol_violation is True
    # Protocol violations are routed via F.3, not the retry budget.
    assert result.is_failure is False


def test_classify_nonzero_exit() -> None:
    result = classify_worker_exit(return_code=137)
    assert result.outcome == "nonzero_exit"
    assert result.is_protocol_violation is False
    assert result.is_failure is True


def test_classify_signaled() -> None:
    result = classify_worker_exit(return_code=0, was_signaled=True)
    assert result.outcome == "signaled"
    assert result.is_failure is True


def test_classify_timeout_killed_takes_priority_over_other_flags() -> None:
    """If we killed it via our own timeout machinery, that's the outcome."""
    result = classify_worker_exit(
        return_code=-9, was_signaled=True, was_timeout_killed=True
    )
    assert result.outcome == "timeout_killed"
    assert result.is_failure is True


def test_classify_return_code_none_treated_as_nonzero() -> None:
    """None rc (e.g. process killed before we could read it) is a failure."""
    result = classify_worker_exit(return_code=None)
    assert result.outcome == "nonzero_exit"
    assert result.is_failure is True


def test_protocol_violation_reasons_has_three_sites() -> None:
    assert set(PROTOCOL_VIOLATION_REASONS.keys()) == {"cron", "vp_cli", "demo"}
    for value in PROTOCOL_VIOLATION_REASONS.values():
        assert value.startswith("protocol_violation_")
        assert value.endswith("_clean_exit_no_disposition")


# ── F.1: cancelled_mid_run outcome (2026-05-13 follow-up) ────────────────────
# Added after the 2026-05-12 → 2026-05-13 gateway-freeze incident where the
# session reaper cancelled stuck LLM-cron coroutines (asyncio.CancelledError)
# and the F.1 classifier mis-painted them as clean_exit_zero. See
# plans/2026-05-13_proactivity_gap_findings.md Contributing Factor #3.


def test_classify_cancelled_mid_run_basic() -> None:
    """``was_cancelled=True`` is a distinct outcome from timeout/signaled.

    Models the case where the gateway's session reaper called
    ``task.cancel()`` on the in-process LLM coroutine because the 600s
    TTL elapsed. The coroutine raised ``asyncio.CancelledError``; the
    caller flagged it and passed ``was_cancelled=True`` here.
    """
    result = classify_worker_exit(
        return_code=0, was_cancelled=True, task_closed_normally=True
    )
    assert result.outcome == "cancelled_mid_run"
    assert result.is_protocol_violation is False
    # Cancellation is a real failure for the operator's retry budget.
    # The run did not complete its mission (briefing didn't go out,
    # paper-to-podcast didn't dispatch, etc.).
    assert result.is_failure is True


def test_classify_cancelled_takes_priority_over_clean_exit_zero() -> None:
    """``was_cancelled`` wins over rc=0 + task_closed_normally.

    The original incident: a reaped session had rc_equiv=0 (falsy
    timeout/exception flags) and task_closed_normally was True
    because Simone had manually cancelled the cron Task Hub item via
    ``task_hub_task_action`` before the classifier ran. Without the
    cancellation flag the classifier would say ``clean_exit_zero``
    (success!) — the opposite of the truth.
    """
    result = classify_worker_exit(
        return_code=0,
        was_cancelled=True,
        task_closed_normally=True,
        was_timeout_killed=False,
        was_signaled=False,
    )
    assert result.outcome == "cancelled_mid_run"
    assert result.is_failure is True


def test_classify_cancelled_takes_priority_over_timeout_killed() -> None:
    """If both flags somehow true, cancellation wins.

    Timeout = UA's own ``asyncio.wait_for`` raising TimeoutError.
    Cancellation = external ``task.cancel()`` (typically session reaper).
    These should be mutually exclusive in practice, but if a race
    produces both, ``cancelled_mid_run`` is the more informative
    classification — it tells the operator the kill came from outside
    UA's own timeout machinery.
    """
    result = classify_worker_exit(
        return_code=0,
        was_cancelled=True,
        was_timeout_killed=True,
    )
    assert result.outcome == "cancelled_mid_run"


def test_classify_cancelled_with_no_task_closure_not_protocol_violation() -> None:
    """Cancellation pre-empts the protocol-violation check.

    A cancelled coroutine never had a chance to close its task —
    that's the cancellation, not a protocol violation. The
    ``cancelled_mid_run`` outcome is the right signal; F.3's
    "needs_review" path is for genuinely silent successes
    (rc=0 + no close), which is a different operational concern.
    """
    result = classify_worker_exit(
        return_code=0,
        was_cancelled=True,
        task_closed_normally=False,
    )
    assert result.outcome == "cancelled_mid_run"
    assert result.is_protocol_violation is False


def test_classify_default_was_cancelled_false_preserves_old_paths() -> None:
    """Existing callers that don't pass ``was_cancelled`` keep old behavior.

    Guards against accidentally regressing the four pre-existing
    classification paths now that ``was_cancelled`` is in the signature.
    """
    # rc=0 + closed → clean_exit_zero
    r1 = classify_worker_exit(return_code=0, task_closed_normally=True)
    assert r1.outcome == "clean_exit_zero"
    # rc=0 + not-closed → protocol violation
    r2 = classify_worker_exit(return_code=0, task_closed_normally=False)
    assert r2.outcome == "clean_exit_zero_no_disposition"
    # rc != 0 → nonzero_exit
    r3 = classify_worker_exit(return_code=1)
    assert r3.outcome == "nonzero_exit"
    # timeout
    r4 = classify_worker_exit(return_code=-9, was_timeout_killed=True)
    assert r4.outcome == "timeout_killed"


# ── F.2: max_runtime_seconds column ────────────────────────────────────────


def test_max_runtime_seconds_column_exists(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(task_hub_items)")}
    assert "max_runtime_seconds" in cols


def test_max_runtime_seconds_round_trips(conn: sqlite3.Connection) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": "task:f2",
            "source_kind": "internal",
            "title": "long-running",
            "max_runtime_seconds": 600,
        },
    )
    row = task_hub.get_item(conn, "task:f2")
    assert row is not None
    assert row["max_runtime_seconds"] == 600


def test_max_runtime_seconds_preserved_on_re_upsert(conn: sqlite3.Connection) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": "task:f2b",
            "source_kind": "internal",
            "title": "x",
            "max_runtime_seconds": 1200,
        },
    )
    # Re-upsert without max_runtime_seconds — must inherit existing.
    task_hub.upsert_item(conn, {"task_id": "task:f2b", "title": "y"})
    row = task_hub.get_item(conn, "task:f2b")
    assert row is not None
    assert row["max_runtime_seconds"] == 1200


def test_max_runtime_seconds_invalid_value_normalized_to_null(conn: sqlite3.Connection) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": "task:f2c",
            "source_kind": "internal",
            "title": "x",
            "max_runtime_seconds": "garbage",
        },
    )
    row = task_hub.get_item(conn, "task:f2c")
    assert row is not None
    assert row["max_runtime_seconds"] is None


# ── F.2: resolve_max_runtime_seconds ────────────────────────────────────────


def test_resolve_returns_per_task_value_when_set(monkeypatch) -> None:
    monkeypatch.delenv("UA_TASK_DEFAULT_MAX_RUNTIME_SECONDS", raising=False)
    task = {"max_runtime_seconds": 900}
    assert task_hub.resolve_max_runtime_seconds(task) == 900


def test_resolve_falls_through_to_env(monkeypatch) -> None:
    monkeypatch.setenv("UA_TASK_DEFAULT_MAX_RUNTIME_SECONDS", "1800")
    assert task_hub.resolve_max_runtime_seconds(None) == 1800
    assert task_hub.resolve_max_runtime_seconds({}) == 1800


def test_resolve_falls_through_to_default(monkeypatch) -> None:
    monkeypatch.delenv("UA_TASK_DEFAULT_MAX_RUNTIME_SECONDS", raising=False)
    assert task_hub.resolve_max_runtime_seconds(None) == 7200


def test_resolve_task_value_wins_over_env(monkeypatch) -> None:
    monkeypatch.setenv("UA_TASK_DEFAULT_MAX_RUNTIME_SECONDS", "1800")
    assert task_hub.resolve_max_runtime_seconds({"max_runtime_seconds": 300}) == 300


def test_resolve_invalid_per_task_falls_through_to_env(monkeypatch) -> None:
    monkeypatch.setenv("UA_TASK_DEFAULT_MAX_RUNTIME_SECONDS", "1800")
    assert task_hub.resolve_max_runtime_seconds({"max_runtime_seconds": "garbage"}) == 1800


def test_resolve_zero_or_negative_falls_through() -> None:
    """Zero or negative timeouts make no operational sense — ignore them."""
    assert task_hub.resolve_max_runtime_seconds({"max_runtime_seconds": 0}) == 7200
    assert task_hub.resolve_max_runtime_seconds({"max_runtime_seconds": -100}) == 7200
