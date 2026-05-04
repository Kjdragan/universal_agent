"""Pin the contract: terminal Kanban actions on non-proactive tasks
emit an intelligence-grade activity event Mission Control's tier-1
LLM can pick up.

The proactive path (proactive_outcome_tracker) covers proactive-sourced
tasks. This file covers the rest — manually-created Kanban cards,
cron-spawned tasks, mission-spawned tasks. Without this hook, operator
completions on the Kanban board produced no signal the dashboard could
surface.

Tests run with UA_PROACTIVE_OUTCOME_MEMORY=false and
UA_PROACTIVE_AUTO_INVESTIGATE=false so we don't pollute the real
MEMORY.md / memory/index.json files when exercising the proactive
outcome path. (perform_task_action -> record_proactive_outcome ->
_write_outcome_to_memory writes to the repo-level memory dir by
default; in production that's correct, in tests it leaks state.)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from universal_agent import task_hub


@pytest.fixture(autouse=True)
def _isolate_proactive_outcome_side_effects(monkeypatch):
    monkeypatch.setenv("UA_PROACTIVE_OUTCOME_MEMORY", "false")
    monkeypatch.setenv("UA_PROACTIVE_AUTO_INVESTIGATE", "false")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_task(conn: sqlite3.Connection, *, task_id: str, source_kind: str = "manual") -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": source_kind,
            "source_ref": f"ref-{task_id}",
            "title": f"Test task {task_id}",
            "description": "lifecycle test",
            "project_key": "test_project",
            "priority": 2,
            "labels": ["test"],
        },
    )


def _read_activity_events(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Table may not exist if nothing was ever emitted — tolerate that.
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='activity_events'"
        ).fetchone()
        if not existing:
            return []
        rows = conn.execute("SELECT * FROM activity_events").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def test_kanban_complete_on_manual_task_emits_success_event(tmp_path, monkeypatch):
    """Operator completes a manually-created Kanban card → tier-1
    should see a `task_hub.task_completed` success event."""
    activity_db = tmp_path / "activity.db"
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(activity_db))

    conn = _conn()
    try:
        _seed_task(conn, task_id="manual-1", source_kind="manual")
        task_hub.perform_task_action(
            conn,
            task_id="manual-1",
            action="complete",
            reason="finished by operator",
            agent_id="dashboard_operator",
        )
    finally:
        conn.close()

    events = _read_activity_events(activity_db)
    intel = [e for e in events if e["source_domain"] == "task_hub"]
    assert len(intel) == 1
    e = intel[0]
    assert e["kind"] == "task_completed"
    assert e["severity"] == "success"
    assert "manual-1" in e["title"] or "manual-1" in e["metadata_json"]


def test_kanban_block_on_manual_task_emits_warning_event(tmp_path, monkeypatch):
    activity_db = tmp_path / "activity.db"
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(activity_db))

    conn = _conn()
    try:
        _seed_task(conn, task_id="manual-2", source_kind="manual")
        task_hub.perform_task_action(
            conn,
            task_id="manual-2",
            action="block",
            reason="dependency missing",
            agent_id="dashboard_operator",
        )
    finally:
        conn.close()

    events = _read_activity_events(activity_db)
    intel = [e for e in events if e["source_domain"] == "task_hub"]
    assert len(intel) == 1
    assert intel[0]["kind"] == "task_block"
    assert intel[0]["severity"] == "warning"


def test_kanban_complete_on_proactive_task_does_not_double_emit(tmp_path, monkeypatch):
    """Proactive-sourced terminal actions are emitted by the proactive
    outcome tracker. The new task_hub emit path must NOT also fire,
    otherwise the dashboard would see two cards for the same event."""
    activity_db = tmp_path / "activity.db"
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(activity_db))

    conn = _conn()
    try:
        _seed_task(conn, task_id="proac-1", source_kind="csi")
        task_hub.perform_task_action(
            conn,
            task_id="proac-1",
            action="complete",
            reason="proactive task done",
            agent_id="codie",
        )
    finally:
        conn.close()

    events = _read_activity_events(activity_db)
    task_hub_events = [e for e in events if e["source_domain"] == "task_hub"]
    proactive_events = [e for e in events if e["source_domain"] == "proactive_task"]
    # Proactive path should fire exactly once; task_hub generic path
    # must skip because source_kind=csi is in PROACTIVE_SOURCES.
    assert len(task_hub_events) == 0
    assert len(proactive_events) == 1


def test_non_terminal_action_does_not_emit(tmp_path, monkeypatch):
    """A `seize` action is a Kanban move but NOT terminal. We don't
    want to flood the dashboard with every drag — only terminal
    completions/blocks/reviews are intelligence-grade."""
    activity_db = tmp_path / "activity.db"
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(activity_db))

    conn = _conn()
    try:
        _seed_task(conn, task_id="manual-3", source_kind="manual")
        task_hub.perform_task_action(
            conn,
            task_id="manual-3",
            action="seize",
            reason="picking up",
            agent_id="dashboard_operator",
        )
    finally:
        conn.close()

    events = _read_activity_events(activity_db)
    assert len([e for e in events if e["source_domain"] == "task_hub"]) == 0
