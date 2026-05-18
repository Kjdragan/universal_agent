"""Unit tests for services.sdk_timeout_park.

Covers the consecutive-timeout counter, parking on threshold, reset on
non-timeout outcomes, and the best-effort behavior contract (no
exceptions ever propagate to the caller).
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from universal_agent import task_hub as th
from universal_agent.services.sdk_timeout_park import (
    is_sdk_timeout,
    record_sdk_timeout_and_maybe_park,
    reset_sdk_timeout_counter,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """File-backed sqlite so the helper's open-per-call works on shared state."""
    return tmp_path / "task_hub.db"


def _open(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def conn(db_path):
    """Connection used by tests for inspection. Helper opens its own."""
    c = _open(db_path)
    th.ensure_schema(c)
    c.commit()
    yield c
    c.close()


@pytest.fixture
def task_id(conn) -> str:
    """Create a fresh task_hub item and return its id."""
    tid = "task-sdk-timeout-test-001"
    th.upsert_item(
        conn,
        {
            "task_id": tid,
            "source_kind": "test",
            "title": "test mission",
            "description": "fixture",
            "status": "in_progress",
        },
    )
    conn.commit()
    return tid


@pytest.fixture
def patch_conn(conn, db_path):
    """Patch gateway_server._task_hub_open_conn so each helper call opens
    its own connection to the same shared file DB. Mirrors production
    behavior (each call opens + closes its own conn)."""
    def _factory():
        return _open(db_path)

    with patch(
        "universal_agent.gateway_server._task_hub_open_conn",
        side_effect=_factory,
    ):
        yield conn


# ---------------------------------------------------------------------------
# 1. is_sdk_timeout pattern matching
# ---------------------------------------------------------------------------
def test_is_sdk_timeout_matches_execution_timeout():
    assert is_sdk_timeout("Execution timed out after 300.0s") is True


def test_is_sdk_timeout_matches_processturnadapter_phrase():
    assert is_sdk_timeout("ProcessTurnAdapter timed out after 300.0s") is True


def test_is_sdk_timeout_is_case_insensitive():
    assert is_sdk_timeout("EXECUTION TIMED OUT AFTER 5s") is True


def test_is_sdk_timeout_negative_unrelated_error():
    assert is_sdk_timeout("permission denied") is False
    assert is_sdk_timeout("model not found") is False
    assert is_sdk_timeout("") is False
    assert is_sdk_timeout(None) is False  # type: ignore[arg-type]


def test_is_sdk_timeout_does_not_match_generic_timeout_word():
    # We want substring "timed out after" — not just any "timeout" word,
    # so we don't accidentally park on unrelated subprocess-level timeouts.
    assert is_sdk_timeout("connection timeout") is False
    assert is_sdk_timeout("request timeout while fetching") is False


# ---------------------------------------------------------------------------
# 2. record_sdk_timeout_and_maybe_park — happy paths
# ---------------------------------------------------------------------------
def test_first_timeout_increments_counter_does_not_park(patch_conn, task_id):
    parked, count = record_sdk_timeout_and_maybe_park(
        task_id=task_id,
        mission_id="vp-mission-001",
        error_text="Execution timed out after 300.0s",
        threshold=3,
    )
    assert parked is False
    assert count == 1
    item = th.get_item(patch_conn, task_id)
    assert item["metadata"]["sdk_consecutive_timeouts"] == 1
    assert item["status"] == "in_progress"  # not parked yet


def test_threshold_timeout_parks_task(patch_conn, task_id):
    # First two timeouts: just increment.
    for i in range(2):
        parked, _ = record_sdk_timeout_and_maybe_park(
            task_id=task_id,
            mission_id=f"vp-mission-{i:03d}",
            error_text="Execution timed out after 300.0s",
            threshold=3,
        )
        assert parked is False
    # Third timeout: should park.
    parked, count = record_sdk_timeout_and_maybe_park(
        task_id=task_id,
        mission_id="vp-mission-002",
        error_text="Execution timed out after 300.0s",
        threshold=3,
    )
    assert parked is True
    assert count == 3
    item = th.get_item(patch_conn, task_id)
    assert item["status"] == "needs_review"


def test_threshold_one_parks_immediately(patch_conn, task_id):
    """With threshold=1, even the first timeout parks."""
    parked, count = record_sdk_timeout_and_maybe_park(
        task_id=task_id,
        mission_id="vp-mission-001",
        error_text="Execution timed out after 300.0s",
        threshold=1,
    )
    assert parked is True
    assert count == 1


# ---------------------------------------------------------------------------
# 3. reset_sdk_timeout_counter
# ---------------------------------------------------------------------------
def test_reset_clears_counter(patch_conn, task_id):
    # Set up: two timeouts recorded.
    for i in range(2):
        record_sdk_timeout_and_maybe_park(
            task_id=task_id,
            mission_id=f"vp-mission-{i:03d}",
            error_text="Execution timed out after 300.0s",
            threshold=10,
        )
    item = th.get_item(patch_conn, task_id)
    assert item["metadata"]["sdk_consecutive_timeouts"] == 2

    # Reset.
    reset_sdk_timeout_counter(task_id=task_id, mission_id="vp-mission-002")

    item = th.get_item(patch_conn, task_id)
    assert "sdk_consecutive_timeouts" not in item["metadata"]
    assert "sdk_last_timeout_reason" not in item["metadata"]


def test_reset_is_idempotent_with_no_prior_counter(patch_conn, task_id):
    """Calling reset when no counter exists is a clean no-op (no crash)."""
    reset_sdk_timeout_counter(task_id=task_id, mission_id="vp-mission-001")
    item = th.get_item(patch_conn, task_id)
    # Schema gives metadata={} default; we shouldn't have introduced anything.
    assert item["metadata"] == {} or "sdk_consecutive_timeouts" not in item["metadata"]


# ---------------------------------------------------------------------------
# 4. Non-timeout errors must NOT increment the counter
# ---------------------------------------------------------------------------
def test_non_timeout_error_is_a_no_op_on_record(patch_conn, task_id):
    parked, count = record_sdk_timeout_and_maybe_park(
        task_id=task_id,
        mission_id="vp-mission-001",
        error_text="permission denied",
        threshold=3,
    )
    assert parked is False
    assert count == 0
    item = th.get_item(patch_conn, task_id)
    assert "sdk_consecutive_timeouts" not in item["metadata"]


# ---------------------------------------------------------------------------
# 5. Empty task_id ⇒ silent no-op (the common case for unlinked missions)
# ---------------------------------------------------------------------------
def test_empty_task_id_is_silent_noop():
    parked, count = record_sdk_timeout_and_maybe_park(
        task_id="",
        mission_id="vp-mission-001",
        error_text="Execution timed out after 300.0s",
    )
    assert parked is False
    assert count == 0
    # Should not raise even though we didn't patch the DB conn.
    reset_sdk_timeout_counter(task_id="", mission_id="vp-mission-001")


# ---------------------------------------------------------------------------
# 6. Best-effort: DB errors never raise
# ---------------------------------------------------------------------------
def test_db_open_failure_never_raises():
    """When the task_hub conn can't be opened, helpers swallow the error."""
    with patch(
        "universal_agent.gateway_server._task_hub_open_conn",
        side_effect=RuntimeError("db unavailable"),
    ):
        parked, count = record_sdk_timeout_and_maybe_park(
            task_id="any-task",
            mission_id="vp-mission-001",
            error_text="Execution timed out after 300.0s",
        )
        assert parked is False
        assert count == 0
        # Reset must also swallow.
        reset_sdk_timeout_counter(task_id="any-task", mission_id="vp-mission-001")


# ---------------------------------------------------------------------------
# 7. Unknown task ⇒ silent no-op
# ---------------------------------------------------------------------------
def test_unknown_task_is_silent_noop(patch_conn):
    parked, count = record_sdk_timeout_and_maybe_park(
        task_id="nonexistent-task-id",
        mission_id="vp-mission-001",
        error_text="Execution timed out after 300.0s",
    )
    assert parked is False
    assert count == 0
