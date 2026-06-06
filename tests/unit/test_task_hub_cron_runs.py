"""Tests for per-run cron completion cards (Task Hub Kanban "Completed" lane).

Cron jobs are driven by a single perpetual ``cron:<job>`` row that recycles
``open -> in_progress -> completed -> open``; the per-run audit lives in
``task_hub_runs``. ``list_completed_cron_runs`` surfaces each FINISHED run as
its own reviewable card so the operator can inspect what an individual run did.
"""
from __future__ import annotations

import sqlite3

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _make_cron_item(conn: sqlite3.Connection, *, task_id: str, system_job: str) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "cron_run",
            "source_ref": system_job,
            "title": f"Cron: {system_job}",
            "description": f"Deterministic {system_job} delivery (LLM-independent path).",
            "agent_ready": False,
            "status": task_hub.TASK_STATUS_IN_PROGRESS,
            "metadata": {"cron_owned": True, "system_job": system_job},
        },
    )


def _run(conn: sqlite3.Connection, *, task_id: str, assignment_id: str, outcome: str, summary: str) -> str:
    """Open and close one run via the real lifecycle writers; returns run_id."""
    conn.execute(
        "INSERT INTO task_hub_assignments (assignment_id, task_id, agent_id, state, started_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (assignment_id, task_id, "cron", "running", task_hub._now_iso()),
    )
    run_id = task_hub._open_run(conn, task_id=task_id, assignment_id=assignment_id, agent_id="cron")
    task_hub._close_run(conn, assignment_id=assignment_id, outcome=outcome, summary=summary)
    conn.commit()
    return run_id


def test_finished_cron_run_becomes_one_completed_card() -> None:
    conn = _conn()
    _make_cron_item(conn, task_id="cron:hourly_intel_digest", system_job="hourly_intel_digest")
    run_id = _run(
        conn,
        task_id="cron:hourly_intel_digest",
        assignment_id="asg1",
        outcome="completed",
        summary="Sent digest with 6 items.",
    )

    cards = task_hub.list_completed_cron_runs(conn)
    assert len(cards) == 1
    card = cards[0]
    # Parent task_id is preserved so the card's "Review" drawer shows the full
    # run history for the job; run_id distinguishes this specific run.
    assert card["task_id"] == "cron:hourly_intel_digest"
    assert card["run_id"] == run_id
    assert card["status"] == task_hub.TASK_STATUS_COMPLETED
    assert card["source_kind"] == "cron_run"
    assert card["title"] == "Cron: hourly_intel_digest"
    assert card["completed_at"]  # ended_at set
    assert card["run_outcome"] == "completed"
    # The run summary is surfaced via the synthetic last_assignment the frontend
    # already renders.
    assert card["last_assignment"]["result_summary"] == "Sent digest with 6 items."


def test_in_flight_run_is_excluded_until_finished() -> None:
    conn = _conn()
    _make_cron_item(conn, task_id="cron:nightly_wiki", system_job="nightly_wiki")
    # Open a run but DO NOT close it (ended_at stays NULL) — a run still in flight
    # must not appear in the Completed lane.
    conn.execute(
        "INSERT INTO task_hub_assignments (assignment_id, task_id, agent_id, state, started_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("asg_open", "cron:nightly_wiki", "cron", "running", task_hub._now_iso()),
    )
    task_hub._open_run(conn, task_id="cron:nightly_wiki", assignment_id="asg_open", agent_id="cron")
    conn.commit()

    assert task_hub.list_completed_cron_runs(conn) == []


def test_multiple_runs_one_card_each_newest_first() -> None:
    conn = _conn()
    _make_cron_item(conn, task_id="cron:morning_briefing", system_job="morning_briefing")
    _run(conn, task_id="cron:morning_briefing", assignment_id="asg_a", outcome="completed", summary="run A")
    # Control ended_at ordering deterministically.
    conn.execute(
        "UPDATE task_hub_runs SET ended_at = ? WHERE assignment_id = ?",
        ("2026-06-06T06:00:00+00:00", "asg_a"),
    )
    _run(conn, task_id="cron:morning_briefing", assignment_id="asg_b", outcome="failed", summary="boom")
    conn.execute(
        "UPDATE task_hub_runs SET ended_at = ? WHERE assignment_id = ?",
        ("2026-06-06T07:00:00+00:00", "asg_b"),
    )
    conn.commit()

    cards = task_hub.list_completed_cron_runs(conn)
    assert len(cards) == 2
    # Newest (asg_b, 07:00) first.
    assert cards[0]["last_assignment"]["result_summary"].startswith("boom") or "boom" in cards[0]["last_assignment"]["result_summary"]
    assert cards[0]["run_outcome"] == "failed"
    assert cards[1]["run_outcome"] == "completed"
    # Distinct run_ids, shared parent task_id.
    assert cards[0]["run_id"] != cards[1]["run_id"]
    assert cards[0]["task_id"] == cards[1]["task_id"] == "cron:morning_briefing"


def test_non_cron_completed_items_are_not_returned() -> None:
    conn = _conn()
    # A normal (non-cron) item with a finished run must never leak into the
    # cron-run card list.
    task_hub.upsert_item(
        conn,
        {
            "task_id": "task-normal-1",
            "source_kind": "task_hub",
            "title": "A normal task",
            "agent_ready": True,
            "status": task_hub.TASK_STATUS_COMPLETED,
        },
    )
    conn.execute(
        "INSERT INTO task_hub_assignments (assignment_id, task_id, agent_id, state, started_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("asg_n", "task-normal-1", "atlas", "running", task_hub._now_iso()),
    )
    task_hub._open_run(conn, task_id="task-normal-1", assignment_id="asg_n", agent_id="atlas")
    task_hub._close_run(conn, assignment_id="asg_n", outcome="completed", summary="done")
    conn.commit()

    assert task_hub.list_completed_cron_runs(conn) == []
