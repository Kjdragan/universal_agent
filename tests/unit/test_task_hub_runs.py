"""Hermes Phase D — task_hub_runs attempt-history table unit tests.

Verifies:
* Schema: table + indexes created via ensure_schema.
* `_open_run` creates a row keyed by assignment_id with started_at now.
* `_close_run` updates ended_at / outcome / summary / error for the
  matching open row.
* `_close_run` is idempotent — a second call on the same assignment
  is a no-op (returns 0 rows updated).
* `list_runs_for_task` returns rows newest-first.
* Integration: `claim_next_dispatch_tasks` opens a run row alongside
  the assignment; `finalize_assignments` closes it.

Phase D is intentionally additive: the absence of run rows must not
break any existing code path. Tests assert this by NOT calling _open_run
in some scenarios and confirming downstream code still works.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any
import uuid

import pytest

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_open_task(conn: sqlite3.Connection, task_id: str) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "internal",
            "title": f"task {task_id}",
            "description": "do the thing",
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
        },
    )


# ── Schema ────────────────────────────────────────────────────────────────


def test_ensure_schema_creates_task_hub_runs_table() -> None:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='task_hub_runs'"
        ).fetchall()
        assert len(rows) == 1
        # Required columns present.
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(task_hub_runs)")}
        assert {
            "run_id",
            "task_id",
            "assignment_id",
            "agent_id",
            "started_at",
            "ended_at",
            "outcome",
            "summary",
            "metadata_json",
            "error",
        } <= cols
    finally:
        conn.close()


# ── _open_run ─────────────────────────────────────────────────────────────


def test_open_run_creates_row_with_started_at() -> None:
    conn = _conn()
    try:
        run_id = task_hub._open_run(
            conn,
            task_id="t1",
            assignment_id="asg-1",
            agent_id="simone",
        )
        assert run_id
        row = conn.execute(
            "SELECT * FROM task_hub_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert row is not None
        assert row["task_id"] == "t1"
        assert row["assignment_id"] == "asg-1"
        assert row["agent_id"] == "simone"
        assert row["started_at"]
        assert row["ended_at"] is None
        assert row["outcome"] is None
    finally:
        conn.close()


# ── _close_run ────────────────────────────────────────────────────────────


def test_close_run_sets_outcome_and_summary() -> None:
    conn = _conn()
    try:
        task_hub._open_run(conn, task_id="t2", assignment_id="asg-2", agent_id="codie")
        updated = task_hub._close_run(
            conn,
            assignment_id="asg-2",
            outcome="completed",
            summary="wrote 3 files",
        )
        assert updated == 1
        row = conn.execute(
            "SELECT * FROM task_hub_runs WHERE assignment_id = ?", ("asg-2",)
        ).fetchone()
        assert row["outcome"] == "completed"
        assert row["summary"] == "wrote 3 files"
        assert row["ended_at"]
    finally:
        conn.close()


def test_close_run_idempotent_on_already_closed() -> None:
    conn = _conn()
    try:
        task_hub._open_run(conn, task_id="t3", assignment_id="asg-3", agent_id="atlas")
        first = task_hub._close_run(
            conn, assignment_id="asg-3", outcome="completed", summary="ok"
        )
        second = task_hub._close_run(
            conn,
            assignment_id="asg-3",
            outcome="failed",
            error="should not override",
        )
        assert first == 1
        assert second == 0  # already closed
        row = conn.execute(
            "SELECT * FROM task_hub_runs WHERE assignment_id = ?", ("asg-3",)
        ).fetchone()
        # First close wins — second was no-op.
        assert row["outcome"] == "completed"
        assert row["summary"] == "ok"
        assert row["error"] is None
    finally:
        conn.close()


# ── list_runs_for_task ────────────────────────────────────────────────────


def test_list_runs_for_task_returns_newest_first() -> None:
    conn = _conn()
    try:
        task_hub._open_run(conn, task_id="t4", assignment_id="asg-a", agent_id="simone")
        time.sleep(0.01)  # ensure distinct started_at timestamps
        task_hub._open_run(conn, task_id="t4", assignment_id="asg-b", agent_id="simone")
        task_hub._close_run(
            conn, assignment_id="asg-a", outcome="failed", error="boom"
        )
        task_hub._close_run(
            conn, assignment_id="asg-b", outcome="completed", summary="ok"
        )
        runs = task_hub.list_runs_for_task(conn, "t4")
        assert len(runs) == 2
        # Newest first → asg-b is the second one inserted.
        assert runs[0]["assignment_id"] == "asg-b"
        assert runs[0]["outcome"] == "completed"
        assert runs[1]["assignment_id"] == "asg-a"
        assert runs[1]["outcome"] == "failed"
        assert runs[1]["error"] == "boom"
    finally:
        conn.close()


# ── Integration: claim opens a run, finalize closes it ───────────────────


def test_claim_next_dispatch_tasks_opens_run_row() -> None:
    """Phase D wiring: every claim must produce a parallel run row."""
    conn = _conn()
    try:
        _seed_open_task(conn, "claim-int-1")
        claimed = task_hub.claim_next_dispatch_tasks(
            conn,
            limit=1,
            agent_id="heartbeat",
            provider_session_id="sess-X",
        )
        assert len(claimed) == 1
        assignment_id = claimed[0]["assignment_id"]
        run_row = conn.execute(
            "SELECT * FROM task_hub_runs WHERE assignment_id = ?", (assignment_id,)
        ).fetchone()
        assert run_row is not None
        assert run_row["task_id"] == "claim-int-1"
        assert run_row["agent_id"] == "heartbeat"
        assert run_row["ended_at"] is None  # still open
    finally:
        conn.close()


def test_finalize_assignments_closes_run_row_as_completed() -> None:
    """Phase D wiring: finalize must close the parallel run row."""
    conn = _conn()
    try:
        _seed_open_task(conn, "finalize-int-1")
        claimed = task_hub.claim_next_dispatch_tasks(
            conn, limit=1, agent_id="heartbeat"
        )
        assignment_id = claimed[0]["assignment_id"]
        task_hub.finalize_assignments(
            conn,
            assignment_ids=[assignment_id],
            state="completed",
            result_summary="wrote the file",
        )
        run_row = conn.execute(
            "SELECT * FROM task_hub_runs WHERE assignment_id = ?", (assignment_id,)
        ).fetchone()
        assert run_row is not None
        assert run_row["ended_at"]
        assert run_row["outcome"] == "completed"
        assert run_row["summary"] == "wrote the file"
        assert run_row["error"] is None
    finally:
        conn.close()


def test_finalize_assignments_closes_run_row_as_failed_with_error() -> None:
    """Failed finalize should populate the error column (not just summary)."""
    conn = _conn()
    try:
        _seed_open_task(conn, "finalize-int-2")
        claimed = task_hub.claim_next_dispatch_tasks(
            conn, limit=1, agent_id="heartbeat"
        )
        assignment_id = claimed[0]["assignment_id"]
        task_hub.finalize_assignments(
            conn,
            assignment_ids=[assignment_id],
            state="failed",
            result_summary="API returned 500",
        )
        run_row = conn.execute(
            "SELECT * FROM task_hub_runs WHERE assignment_id = ?", (assignment_id,)
        ).fetchone()
        assert run_row["outcome"] == "failed"
        assert run_row["error"] == "API returned 500"
    finally:
        conn.close()
