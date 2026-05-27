"""Unit tests for task_hub.find_recent_active_task_for_agent (PR #490c).

Auto-discovery helper used by ``_vp_dispatch_mission_impl`` to recover
the originating Task Hub ``task_id`` when the caller (typically
Simone's LLM via the ``vp_dispatch_mission`` tool) didn't include it
in the args. Without this, the PR #490 cody_session_id write-back +
DelegationTracePanel silently no-op on every operator-dispatched
Cody mission (verified empirically on
``vp-mission-5cedd30dd387a10374b88359``, 2026-05-27 04:30 UTC).
"""

from __future__ import annotations

import sqlite3

from universal_agent.task_hub import find_recent_active_task_for_agent


def _make_conn() -> sqlite3.Connection:
    """In-memory conn with just enough schema for the helper."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE task_hub_assignments (
            assignment_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            state TEXT NOT NULL,
            started_at TEXT NOT NULL
        )
        """
    )
    return conn


def _seize(
    conn: sqlite3.Connection,
    *,
    assignment_id: str,
    task_id: str,
    agent_id: str,
    started_at: str,
    state: str = "seized",
) -> None:
    conn.execute(
        "INSERT INTO task_hub_assignments (assignment_id, task_id, agent_id, state, started_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (assignment_id, task_id, agent_id, state, started_at),
    )


def test_finds_seized_assignment_by_agent_slug():
    """Substring match on agent_id when slug is provided."""
    conn = _make_conn()
    _seize(
        conn,
        assignment_id="asg_1",
        task_id="qa-target",
        agent_id="todo:daemon_simone_todo",
        started_at="2026-05-27T04:27:00",
    )
    assert (
        find_recent_active_task_for_agent(conn, agent_slug="daemon_simone_todo")
        == "qa-target"
    )


def test_returns_most_recent_when_multiple_concurrent_claims():
    """Most-recent ``started_at`` wins."""
    conn = _make_conn()
    _seize(conn, assignment_id="a1", task_id="qa-old", agent_id="todo:daemon_simone_todo", started_at="2026-05-27T04:20:00")
    _seize(conn, assignment_id="a2", task_id="qa-new", agent_id="todo:daemon_simone_todo", started_at="2026-05-27T04:27:00")
    assert (
        find_recent_active_task_for_agent(conn, agent_slug="daemon_simone_todo")
        == "qa-new"
    )


def test_ignores_completed_assignments():
    """Only ``state='seized'`` rows count — completed/failed are skipped."""
    conn = _make_conn()
    _seize(conn, assignment_id="a1", task_id="qa-completed", agent_id="todo:daemon_simone_todo", started_at="2026-05-27T04:30:00", state="completed")
    _seize(conn, assignment_id="a2", task_id="qa-failed", agent_id="todo:daemon_simone_todo", started_at="2026-05-27T04:31:00", state="failed")
    _seize(conn, assignment_id="a3", task_id="qa-current", agent_id="todo:daemon_simone_todo", started_at="2026-05-27T04:25:00", state="seized")
    assert (
        find_recent_active_task_for_agent(conn, agent_slug="daemon_simone_todo")
        == "qa-current"
    )


def test_empty_slug_returns_latest_seized_overall():
    """Production case: no caller context → match any orchestrator."""
    conn = _make_conn()
    _seize(conn, assignment_id="a1", task_id="qa-atlas", agent_id="todo:daemon_atlas_todo", started_at="2026-05-27T04:20:00")
    _seize(conn, assignment_id="a2", task_id="qa-simone-latest", agent_id="todo:daemon_simone_todo", started_at="2026-05-27T04:27:00")
    assert find_recent_active_task_for_agent(conn, agent_slug="") == "qa-simone-latest"
    # Whitespace-only slug also falls into the unfiltered branch.
    assert find_recent_active_task_for_agent(conn, agent_slug="   ") == "qa-simone-latest"


def test_returns_empty_string_when_no_seized_rows():
    """No active claims → no linkage, callers skip propagation silently."""
    conn = _make_conn()
    # One completed row should NOT count as a match.
    _seize(conn, assignment_id="a1", task_id="qa-done", agent_id="todo:daemon_simone_todo", started_at="2026-05-27T04:00:00", state="completed")
    assert find_recent_active_task_for_agent(conn, agent_slug="daemon_simone_todo") == ""
    assert find_recent_active_task_for_agent(conn, agent_slug="") == ""


def test_slug_match_does_not_leak_across_agents():
    """A ``simone`` slug doesn't accidentally match a Cody-owned assignment."""
    conn = _make_conn()
    _seize(conn, assignment_id="a1", task_id="qa-cody", agent_id="vp.coder.primary", started_at="2026-05-27T04:27:00")
    assert find_recent_active_task_for_agent(conn, agent_slug="daemon_simone_todo") == ""
