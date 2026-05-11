"""Hermes Phase B.2 — failure-context dashboard endpoint unit tests.

Verifies the GET /api/v1/dashboard/todolist/tasks/{task_id}/failure-context
handler added in gateway_server.py to surface operator-facing rehydrate context
for tasks wedged in needs_review / blocked. Exercises the same code path as
Phase B.1's perform_task_action -> _rehydrate_task -> _summarize_prior_assignments
to confirm the wiring is end-to-end.

Tests call the handler coroutine directly (bypassing FastAPI's TestClient) so
we get fast, deterministic unit coverage without spinning up the gateway
lifespan. This mirrors the B.1 unit-test style in
``tests/unit/test_task_hub_unstick_verbs.py``.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

import pytest
from fastapi import HTTPException

from universal_agent import task_hub
from universal_agent.gateway_server import (
    dashboard_todolist_get_failure_context,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def seeded_db(tmp_path, monkeypatch) -> sqlite3.Connection:
    """Point the handler at a tmp activity.db with task_hub schema applied.

    The endpoint resolves `UA_ACTIVITY_DB_PATH` lazily inside `_activity_connect`,
    so a `monkeypatch.setenv` is sufficient to redirect IO at the right level.
    """
    db_path = str(tmp_path / "activity.db")
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    yield conn
    conn.close()


def _seed_wedged_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    status: str = task_hub.TASK_STATUS_REVIEW,
    heartbeat_retry_count: int = 3,
    todo_retry_count: int = 2,
    last_disposition_reason: str = "heartbeat_retry_exhausted",
    last_disposition: str = "review",
    last_side_effect_summary: str = "wrote 3 files",
    max_retries: int | None = None,
    extra_dispatch_metadata: dict[str, Any] | None = None,
) -> dict:
    """Mirror of B.1's ``_seed_wedged_task`` (test_task_hub_unstick_verbs.py)."""
    dispatch_meta: dict[str, Any] = {
        "heartbeat_retry_count": heartbeat_retry_count,
        "todo_retry_count": todo_retry_count,
        "last_disposition_reason": last_disposition_reason,
        "last_disposition": last_disposition,
        "last_side_effect_summary": last_side_effect_summary,
    }
    if extra_dispatch_metadata:
        dispatch_meta.update(extra_dispatch_metadata)
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "internal",
            "title": "Wedged for B.2 endpoint test",
            "status": status,
            "agent_ready": True,
            "max_retries": max_retries,
            "metadata": {"dispatch": dispatch_meta},
        },
    )
    return task_hub.get_item(conn, task_id)


def _call(task_id: str) -> dict:
    return asyncio.run(dashboard_todolist_get_failure_context(task_id))


# ── 1. 404 for non-existent task ────────────────────────────────────────────


def test_returns_404_for_missing_task(seeded_db: sqlite3.Connection) -> None:
    with pytest.raises(HTTPException) as exc:
        _call("task:does-not-exist")
    assert exc.value.status_code == 404


# ── 2. Fresh open task returns zeroed counters ──────────────────────────────


def test_returns_zero_counters_for_fresh_open_task(seeded_db: sqlite3.Connection) -> None:
    task_hub.upsert_item(
        seeded_db,
        {
            "task_id": "task:fresh",
            "source_kind": "internal",
            "title": "Fresh task",
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
        },
    )
    payload = _call("task:fresh")
    assert payload["task_id"] == "task:fresh"
    assert payload["status"] == task_hub.TASK_STATUS_OPEN
    assert payload["heartbeat_retry_count"] == 0
    assert payload["todo_retry_count"] == 0
    assert payload["last_disposition"] == ""
    assert payload["last_disposition_reason"] == ""
    assert payload["last_side_effect_summary"] == ""
    assert payload["re_evaluation_context"] is None
    assert payload["revision_round"] == 0
    assert payload["prior_assignments"] == []


# ── 3. Wedged needs_review task surfaces full failure context ───────────────


def test_returns_populated_payload_for_wedged_needs_review_task(
    seeded_db: sqlite3.Connection,
) -> None:
    _seed_wedged_task(seeded_db, task_id="task:wedged")
    payload = _call("task:wedged")
    assert payload["status"] == task_hub.TASK_STATUS_REVIEW
    assert payload["heartbeat_retry_count"] == 3
    assert payload["todo_retry_count"] == 2
    assert payload["last_disposition"] == "review"
    assert payload["last_disposition_reason"] == "heartbeat_retry_exhausted"
    assert payload["last_side_effect_summary"] == "wrote 3 files"
    assert payload["revision_round"] == 0


# ── 4. re_evaluate verb leaves a re_evaluation_context block ────────────────


def test_returns_re_evaluation_context_after_re_evaluate_verb(
    seeded_db: sqlite3.Connection,
) -> None:
    _seed_wedged_task(seeded_db, task_id="task:reeval")
    task_hub.perform_task_action(
        seeded_db,
        task_id="task:reeval",
        action="re_evaluate",
    )
    payload = _call("task:reeval")
    # B.1's re_evaluate attaches structured failure context for Simone.
    assert payload["re_evaluation_context"] is not None
    assert isinstance(payload["re_evaluation_context"], dict)


# ── 5. request_revision verb bumps revision_round ──────────────────────────


def test_returns_incremented_revision_round_after_request_revision(
    seeded_db: sqlite3.Connection,
) -> None:
    _seed_wedged_task(seeded_db, task_id="task:rev", max_retries=5)
    task_hub.perform_task_action(
        seeded_db,
        task_id="task:rev",
        action="request_revision",
        note="please tighten the validation",
    )
    payload = _call("task:rev")
    assert payload["revision_round"] > 0


# ── 6. prior_assignments rows have expected shape, newest-first order ──────


def test_prior_assignments_returns_expected_shape_newest_first(
    seeded_db: sqlite3.Connection,
) -> None:
    _seed_wedged_task(seeded_db, task_id="task:assign")
    # Insert two assignments at different timestamps; expect newest first.
    seeded_db.executemany(
        """
        INSERT INTO task_hub_assignments
            (assignment_id, task_id, agent_id, state, started_at, ended_at, result_summary)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "asg-old",
                "task:assign",
                "agent:a",
                "failed",
                "2026-05-11T10:00:00+00:00",
                "2026-05-11T10:05:00+00:00",
                "first attempt failed",
            ),
            (
                "asg-new",
                "task:assign",
                "agent:b",
                "failed",
                "2026-05-11T11:00:00+00:00",
                "2026-05-11T11:05:00+00:00",
                "second attempt failed",
            ),
        ],
    )
    seeded_db.commit()

    payload = _call("task:assign")
    rows = payload["prior_assignments"]
    assert len(rows) == 2
    # Newest first.
    assert rows[0]["assignment_id"] == "asg-new"
    assert rows[1]["assignment_id"] == "asg-old"
    # Required keys.
    for row in rows:
        assert set(row.keys()) >= {
            "assignment_id",
            "agent_id",
            "state",
            "started_at",
            "ended_at",
            "result_summary",
        }


# ── 7. Phase D.2 — prior_runs surfaced from task_hub_runs ───────────────────


def test_prior_runs_returns_closed_runs_newest_first(
    seeded_db: sqlite3.Connection,
) -> None:
    """Phase D.2 — `prior_runs` reads `task_hub_runs` (per-attempt history).

    Distinct from `prior_assignments`, which is the claim ledger. Runs
    carry the closing outcome/summary/error per attempt; these are what
    Simone (and the operator) need to judge from.
    """
    _seed_wedged_task(seeded_db, task_id="task:runs")
    # Manually open + close two runs (older first, then newer) to mirror
    # what claim_next_dispatch_tasks + finalize_assignments would record
    # for a task that was attempted twice.
    task_hub._open_run(
        seeded_db,
        task_id="task:runs",
        assignment_id="asg-r-old",
        agent_id="agent:a",
    )
    seeded_db.execute(
        "UPDATE task_hub_runs SET started_at=? WHERE assignment_id=?",
        ("2026-05-11T10:00:00+00:00", "asg-r-old"),
    )
    task_hub._close_run(
        seeded_db,
        assignment_id="asg-r-old",
        outcome="failed",
        summary="first attempt failed",
        error="boom",
    )
    task_hub._open_run(
        seeded_db,
        task_id="task:runs",
        assignment_id="asg-r-new",
        agent_id="agent:b",
    )
    seeded_db.execute(
        "UPDATE task_hub_runs SET started_at=? WHERE assignment_id=?",
        ("2026-05-11T11:00:00+00:00", "asg-r-new"),
    )
    task_hub._close_run(
        seeded_db,
        assignment_id="asg-r-new",
        outcome="failed",
        summary="second attempt also failed",
        error="kaboom",
    )
    seeded_db.commit()

    payload = _call("task:runs")
    runs = payload["prior_runs"]
    assert len(runs) == 2
    # Newest first.
    assert runs[0]["assignment_id"] == "asg-r-new"
    assert runs[1]["assignment_id"] == "asg-r-old"
    # Closing fields populated.
    assert runs[0]["outcome"] == "failed"
    assert runs[0]["summary"] == "second attempt also failed"
    assert runs[0]["error"] == "kaboom"
    # Required keys (matches `list_runs_for_task` shape).
    for row in runs:
        assert set(row.keys()) >= {
            "run_id",
            "task_id",
            "assignment_id",
            "agent_id",
            "started_at",
            "ended_at",
            "outcome",
            "summary",
            "error",
            "metadata",
        }


def test_prior_runs_empty_when_no_runs_recorded(
    seeded_db: sqlite3.Connection,
) -> None:
    """D.2 rollout is additive — absence of runs must yield empty list."""
    _seed_wedged_task(seeded_db, task_id="task:no-runs")
    payload = _call("task:no-runs")
    assert payload["prior_runs"] == []


def test_re_evaluate_attaches_prior_runs_into_context(
    seeded_db: sqlite3.Connection,
) -> None:
    """D.2 — re_evaluate verb embeds `prior_runs` in re_evaluation_context.

    Simone's prompt assembler will read this on her next claim; the
    failing attempts give her evidence to judge from.
    """
    _seed_wedged_task(seeded_db, task_id="task:reeval-runs")
    # Seed one closed failing run.
    task_hub._open_run(
        seeded_db,
        task_id="task:reeval-runs",
        assignment_id="asg-pre",
        agent_id="agent:a",
    )
    task_hub._close_run(
        seeded_db,
        assignment_id="asg-pre",
        outcome="failed",
        summary="prior attempt blew up",
        error="permission denied",
    )
    seeded_db.commit()

    task_hub.perform_task_action(
        seeded_db,
        task_id="task:reeval-runs",
        action="re_evaluate",
    )
    payload = _call("task:reeval-runs")
    ctx = payload["re_evaluation_context"]
    assert isinstance(ctx, dict)
    assert "prior_runs" in ctx
    assert isinstance(ctx["prior_runs"], list)
    assert len(ctx["prior_runs"]) == 1
    assert ctx["prior_runs"][0]["outcome"] == "failed"
    assert ctx["prior_runs"][0]["error"] == "permission denied"
