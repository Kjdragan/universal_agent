"""Unit tests for needs_review_reaper.

Covers the SLA recovery sweep that re-opens disposition-uncertain
``needs_review`` tasks. Operator-gated dispositions and unknown
dispositions must be left alone.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
from unittest import mock

import pytest

from universal_agent import task_hub
from universal_agent.services import needs_review_reaper


@pytest.fixture
def conn() -> sqlite3.Connection:
    """In-memory DB with the task_hub schema initialized."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    task_hub.ensure_schema(c)
    return c


def _insert_review_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    disposition_reason: str,
    updated_at: str,
    extra_dispatch: dict | None = None,
) -> None:
    """Insert a fake needs_review task with the given disposition reason."""
    dispatch = {
        "last_disposition": "needs_review",
        "last_disposition_reason": disposition_reason,
    }
    if extra_dispatch:
        dispatch.update(extra_dispatch)
    metadata = {"dispatch": dispatch}
    conn.execute(
        """
        INSERT INTO task_hub_items (
            task_id, source_kind, source_ref, title, description, project_key,
            priority, labels_json, status, metadata_json, created_at, updated_at
        ) VALUES (?, 'proactive_signal', '', ?, '', 'proactive', 2, '[]', ?, ?, ?, ?)
        """,
        (
            task_id,
            f"test task {task_id}",
            task_hub.TASK_STATUS_REVIEW,
            json.dumps(metadata),
            updated_at,
            updated_at,
        ),
    )
    conn.commit()


def _now() -> datetime:
    return datetime(2026, 5, 18, 18, 0, 0, tzinfo=timezone.utc)


def _age(now: datetime, *, hours: float) -> str:
    return (now - timedelta(hours=hours)).isoformat()


def test_recovers_disposition_uncertain_task_past_sla(conn):
    now = _now()
    _insert_review_task(
        conn,
        task_id="t-uncertain-old",
        disposition_reason="todo_completed_without_disposition",
        updated_at=_age(now, hours=5),
    )

    result = needs_review_reaper.reap_stale_needs_review(conn, now=now)

    assert result["recovered"] == 1
    assert result["recovered_ids"] == ["t-uncertain-old"]
    row = conn.execute(
        "SELECT status, metadata_json FROM task_hub_items WHERE task_id=?",
        ("t-uncertain-old",),
    ).fetchone()
    assert row["status"] == task_hub.TASK_STATUS_OPEN
    meta = json.loads(row["metadata_json"])
    assert meta["dispatch"]["last_disposition"] == "reopened"
    assert meta["dispatch"]["last_disposition_reason"] == "needs_review_sla_recovered"
    assert meta["dispatch"]["needs_review_prior_reason"] == "todo_completed_without_disposition"
    assert meta["dispatch"]["needs_review_recovery_count"] == 1


def test_leaves_task_inside_sla_window(conn):
    now = _now()
    _insert_review_task(
        conn,
        task_id="t-fresh",
        disposition_reason="todo_completed_without_disposition",
        updated_at=_age(now, hours=2),  # 2h < 4h SLA
    )

    result = needs_review_reaper.reap_stale_needs_review(conn, now=now)

    assert result["recovered"] == 0
    row = conn.execute(
        "SELECT status FROM task_hub_items WHERE task_id=?",
        ("t-fresh",),
    ).fetchone()
    assert row["status"] == task_hub.TASK_STATUS_REVIEW


def test_skips_operator_gated_dispositions(conn):
    now = _now()
    for tid, reason in [
        ("t-gated-1", "heartbeat_retry_exhausted"),
        ("t-gated-2", "heartbeat_retryable_with_side_effects"),
        ("t-gated-3", "todo_retryable_with_side_effects"),
    ]:
        _insert_review_task(conn, task_id=tid, disposition_reason=reason, updated_at=_age(now, hours=24))

    result = needs_review_reaper.reap_stale_needs_review(conn, now=now)

    assert result["recovered"] == 0
    assert result["skipped_gated"] == 3
    for tid in ("t-gated-1", "t-gated-2", "t-gated-3"):
        row = conn.execute(
            "SELECT status FROM task_hub_items WHERE task_id=?", (tid,),
        ).fetchone()
        assert row["status"] == task_hub.TASK_STATUS_REVIEW


def test_skips_unknown_disposition_reasons(conn):
    """Unknown reasons might be from third-party / legacy / future code paths."""
    now = _now()
    _insert_review_task(
        conn,
        task_id="t-unknown",
        disposition_reason="some_future_reason_we_dont_know",
        updated_at=_age(now, hours=24),
    )

    result = needs_review_reaper.reap_stale_needs_review(conn, now=now)

    assert result["recovered"] == 0
    assert result["skipped_unknown"] == 1


def test_handles_missing_metadata_gracefully(conn):
    """A task with no dispatch metadata should be skipped, not error."""
    now = _now()
    conn.execute(
        """
        INSERT INTO task_hub_items (
            task_id, source_kind, source_ref, title, description, project_key,
            priority, labels_json, status, metadata_json, created_at, updated_at
        ) VALUES (?, 'manual', '', 'no metadata', '', 'proactive', 2, '[]', ?, '{}', ?, ?)
        """,
        ("t-bare", task_hub.TASK_STATUS_REVIEW, _age(now, hours=24), _age(now, hours=24)),
    )
    conn.commit()

    result = needs_review_reaper.reap_stale_needs_review(conn, now=now)

    assert result["recovered"] == 0
    # Bare metadata has no reason → falls into the "unknown" bucket, not gated.
    assert result["skipped_unknown"] == 1


def test_recovery_count_increments_across_runs(conn):
    """A task that keeps falling back to needs_review should accumulate count."""
    now = _now()
    _insert_review_task(
        conn,
        task_id="t-loop",
        disposition_reason="todo_completed_without_disposition",
        updated_at=_age(now, hours=24),
        extra_dispatch={"needs_review_recovery_count": 2},
    )

    result = needs_review_reaper.reap_stale_needs_review(conn, now=now)
    assert result["recovered"] == 1
    row = conn.execute(
        "SELECT metadata_json FROM task_hub_items WHERE task_id=?",
        ("t-loop",),
    ).fetchone()
    meta = json.loads(row["metadata_json"])
    assert meta["dispatch"]["needs_review_recovery_count"] == 3


def test_honors_per_sweep_limit(conn):
    now = _now()
    for i in range(7):
        _insert_review_task(
            conn,
            task_id=f"t-bulk-{i}",
            disposition_reason="todo_completed_without_disposition",
            updated_at=_age(now, hours=24 + i),
        )

    result = needs_review_reaper.reap_stale_needs_review(conn, now=now, limit=3)
    assert result["recovered"] == 3
    # Oldest-first ordering: bulk-6, bulk-5, bulk-4 (highest "hours" first).
    assert result["recovered_ids"] == ["t-bulk-6", "t-bulk-5", "t-bulk-4"]


def test_env_overrides_for_sla_and_limit(conn):
    now = _now()
    _insert_review_task(
        conn,
        task_id="t-env-1",
        disposition_reason="todo_completed_without_disposition",
        updated_at=_age(now, hours=1.5),  # would NOT recover at 4h SLA
    )

    with mock.patch.dict(os.environ, {"UA_NEEDS_REVIEW_SLA_HOURS": "1"}):
        result = needs_review_reaper.reap_stale_needs_review(conn, now=now)
    assert result["recovered"] == 1
    assert result["sla_hours"] == 1


def test_does_not_recover_tasks_outside_needs_review(conn):
    """Only status=needs_review should be touched."""
    now = _now()
    conn.execute(
        """
        INSERT INTO task_hub_items (
            task_id, source_kind, source_ref, title, description, project_key,
            priority, labels_json, status, metadata_json, created_at, updated_at
        ) VALUES (?, 'proactive_signal', '', 'open task', '', 'proactive', 2, '[]', 'open',
                  '{"dispatch":{"last_disposition_reason":"todo_completed_without_disposition"}}',
                  ?, ?)
        """,
        ("t-open", _age(now, hours=24), _age(now, hours=24)),
    )
    conn.commit()

    result = needs_review_reaper.reap_stale_needs_review(conn, now=now)
    assert result["recovered"] == 0
