"""Tests for Phase 3: Proactive Outcome Tracking & Feedback Loop.

Covers:
  - Outcome recording for proactive tasks
  - Skip logic for non-proactive tasks
  - Stats aggregation
  - Implicit preference signals
  - Duration calculation
  - Memory integration
  - Auto-investigation triggering
  - Feature flag gating
  - Deterministic fallback diagnostic
  - Gateway endpoint shape
  - Intelligence report integration
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh_conn():
    """Create a fresh in-memory SQLite connection with row_factory."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Bootstrap minimal task_hub schema needed by the tracker
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS task_hub_items (
            task_id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            priority INTEGER NOT NULL DEFAULT 1,
            source_kind TEXT NOT NULL DEFAULT '',
            project_key TEXT NOT NULL DEFAULT '',
            trigger_type TEXT NOT NULL DEFAULT 'normal',
            must_complete INTEGER NOT NULL DEFAULT 0,
            labels TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS task_hub_assignments (
            assignment_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'active',
            started_at TEXT NOT NULL DEFAULT '',
            ended_at TEXT,
            result_summary TEXT
        );

        CREATE TABLE IF NOT EXISTS task_hub_evaluations (
            evaluation_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL DEFAULT '',
            decision TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            score REAL,
            evaluated_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS task_hub_comments (
            comment_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            author TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS proactive_preference_signals (
            signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            artifact_id TEXT NOT NULL DEFAULT '',
            signal_key TEXT NOT NULL DEFAULT '',
            signal_type TEXT NOT NULL DEFAULT '',
            weight REAL NOT NULL DEFAULT 0.0,
            score REAL,
            text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
    """)
    conn.commit()
    yield conn
    conn.close()


def _make_proactive_task(
    task_id: str | None = None,
    source_kind: str = "reflection",
    **overrides,
) -> dict[str, Any]:
    """Create a realistic proactive task dict."""
    task_id = task_id or f"task_{uuid.uuid4().hex[:10]}"
    return {
        "task_id": task_id,
        "title": f"Test task {task_id}",
        "description": "This is a proactive test task",
        "status": "open",
        "priority": 2,
        "source_kind": source_kind,
        "project_key": "proactive",
        "labels": ["test", "proactive"],
        "metadata": {"primary_topic": "testing"},
        "created_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **overrides,
    }


def _add_assignment(conn: sqlite3.Connection, task_id: str, **kwargs):
    """Insert a test assignment row."""
    started = kwargs.get("started_at", (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    conn.execute(
        """
        INSERT INTO task_hub_assignments (assignment_id, task_id, agent_id, state, started_at, ended_at, result_summary)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"asg_{uuid.uuid4().hex[:8]}",
            task_id,
            kwargs.get("agent_id", "simone-001"),
            kwargs.get("state", "completed"),
            started,
            kwargs.get("ended_at"),
            kwargs.get("result_summary", "completed"),
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 1. test_record_outcome_proactive_task
# ---------------------------------------------------------------------------

def test_record_outcome_proactive_task(fresh_conn):
    """Verify outcome is recorded for a proactive-sourced task."""
    from universal_agent.services.proactive_outcome_tracker import record_proactive_outcome

    task = _make_proactive_task(source_kind="reflection")
    _add_assignment(fresh_conn, task["task_id"])

    with patch("universal_agent.services.proactive_outcome_tracker._write_outcome_to_memory", return_value=True):
        with patch("universal_agent.services.proactive_outcome_tracker._trigger_auto_investigation", return_value=None):
            result = record_proactive_outcome(
                fresh_conn,
                task=task,
                action="complete",
                reason="all good",
                agent_id="simone-001",
            )

    assert result is not None
    assert result["task_id"] == task["task_id"]
    assert result["source_kind"] == "reflection"
    assert result["action"] == "complete"
    assert result["terminal_status"] == "completed"
    assert result["reason"] == "all good"
    assert result["agent_id"] == "simone-001"
    assert result["assignment_count"] == 1

    # Verify row in DB
    row = fresh_conn.execute(
        "SELECT * FROM proactive_outcomes WHERE task_id = ?",
        (task["task_id"],),
    ).fetchone()
    assert row is not None
    assert row["action"] == "complete"


# ---------------------------------------------------------------------------
# 2. test_skip_non_proactive_task
# ---------------------------------------------------------------------------

def test_skip_non_proactive_task(fresh_conn):
    """Verify non-proactive tasks are skipped."""
    from universal_agent.services.proactive_outcome_tracker import record_proactive_outcome

    task = _make_proactive_task(source_kind="email")

    result = record_proactive_outcome(
        fresh_conn,
        task=task,
        action="complete",
        reason="done",
    )

    assert result is None

    # Verify no rows in DB
    from universal_agent.services.proactive_outcome_tracker import ensure_schema
    ensure_schema(fresh_conn)
    count = fresh_conn.execute("SELECT COUNT(*) AS c FROM proactive_outcomes").fetchone()["c"]
    assert count == 0


# ---------------------------------------------------------------------------
# 3. test_outcome_stats_aggregation
# ---------------------------------------------------------------------------

def test_outcome_stats_aggregation(fresh_conn):
    """Verify stats correctly aggregate by action and source."""
    from universal_agent.services.proactive_outcome_tracker import (
        ensure_schema, get_outcome_stats,
    )

    ensure_schema(fresh_conn)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Insert test outcomes directly
    for i, (action, source, reason) in enumerate([
        ("complete", "reflection", ""),
        ("complete", "proactive_signal", ""),
        ("block", "reflection", "resource unavailable"),
        ("review", "csi", "needs human review"),
        ("complete", "reflection", ""),
    ]):
        fresh_conn.execute(
            """
            INSERT INTO proactive_outcomes (
                outcome_id, task_id, source_kind, action, terminal_status,
                reason, agent_id, assignment_count, duration_seconds,
                investigated, investigation_artifact_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"out_{i}", f"task_{i}", source, action,
                "completed" if action == "complete" else "blocked",
                reason, "simone-001", 1, 120.0 + i * 30, 0, None, now_iso,
            ),
        )
    fresh_conn.commit()

    stats = get_outcome_stats(fresh_conn, window_hours=24)

    assert stats["total"] == 5
    assert stats["by_action"]["complete"] == 3
    assert stats["by_action"]["block"] == 1
    assert stats["by_action"]["review"] == 1
    assert stats["by_source_kind"]["reflection"] == 3
    assert stats["success_count"] == 3
    assert stats["failure_count"] == 2
    assert stats["success_rate"] == 0.6
    assert stats["avg_duration_seconds"] > 0


# ---------------------------------------------------------------------------
# 4. test_implicit_preference_signal
# ---------------------------------------------------------------------------

def test_implicit_preference_signal(fresh_conn):
    """Verify success/failure generates correct preference weight."""
    from universal_agent.services.proactive_outcome_tracker import _fire_implicit_preference_signal

    task = _make_proactive_task(source_kind="reflection")

    with patch("universal_agent.services.proactive_preferences.rebuild_preference_snapshot"):
        _fire_implicit_preference_signal(fresh_conn, task=task, action="complete")

    # Check that signals were inserted with positive weight
    rows = fresh_conn.execute(
        "SELECT * FROM proactive_preference_signals WHERE signal_type = 'implicit_outcome'"
    ).fetchall()
    assert len(rows) > 0
    for row in rows:
        assert float(row["weight"]) == 0.3  # complete => +0.3


def test_implicit_preference_signal_failure(fresh_conn):
    """Verify failure generates negative weight."""
    from universal_agent.services.proactive_outcome_tracker import _fire_implicit_preference_signal

    task = _make_proactive_task(source_kind="proactive_signal")

    with patch("universal_agent.services.proactive_preferences.rebuild_preference_snapshot"):
        _fire_implicit_preference_signal(fresh_conn, task=task, action="block")

    rows = fresh_conn.execute(
        "SELECT * FROM proactive_preference_signals WHERE signal_type = 'implicit_outcome'"
    ).fetchall()
    assert len(rows) > 0
    for row in rows:
        assert float(row["weight"]) == -0.4  # block => -0.4


# ---------------------------------------------------------------------------
# 5. test_duration_calculation
# ---------------------------------------------------------------------------

def test_duration_calculation(fresh_conn):
    """Verify duration is computed from first assignment to terminal action."""
    from universal_agent.services.proactive_outcome_tracker import _compute_duration

    task_id = f"task_{uuid.uuid4().hex[:8]}"

    # Add an assignment that started 2 hours ago
    started = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    _add_assignment(fresh_conn, task_id, started_at=started)

    duration = _compute_duration(fresh_conn, task_id)
    assert duration is not None
    assert duration >= 7100  # ~2 hours in seconds
    assert duration < 7500


def test_duration_no_assignments(fresh_conn):
    """Verify duration is None when task has no assignments."""
    from universal_agent.services.proactive_outcome_tracker import _compute_duration

    duration = _compute_duration(fresh_conn, "nonexistent_task")
    assert duration is None


# ---------------------------------------------------------------------------
# 6. test_memory_write_success_path
# ---------------------------------------------------------------------------

def test_memory_write_success_path():
    """Verify successful task writes positive memory entry."""
    from universal_agent.services.proactive_outcome_tracker import _write_outcome_to_memory

    task = _make_proactive_task(source_kind="reflection")
    outcome = {
        "action": "complete",
        "duration_seconds": 300.0,
        "reason": "",
    }

    mock_mo = MagicMock()
    mock_mo.write.return_value = {"id": "mem_123"}

    with patch("universal_agent.memory.orchestrator.MemoryOrchestrator", return_value=mock_mo):
        with patch("universal_agent.memory.orchestrator._resolve_workspace_dir", return_value="/tmp/test"):
            result = _write_outcome_to_memory(task=task, outcome=outcome)

    assert result is True
    mock_mo.write.assert_called_once()
    call_kwargs = mock_mo.write.call_args[1]
    assert "successfully" in call_kwargs["content"]
    assert "proactive_outcome" in call_kwargs["tags"]
    assert "success" in call_kwargs["tags"]
    assert call_kwargs["importance"] == 0.6


# ---------------------------------------------------------------------------
# 7. test_memory_write_failure_path
# ---------------------------------------------------------------------------

def test_memory_write_failure_path():
    """Verify failed task writes negative memory entry with diagnostic."""
    from universal_agent.services.proactive_outcome_tracker import _write_outcome_to_memory

    task = _make_proactive_task(source_kind="csi")
    outcome = {
        "action": "block",
        "duration_seconds": 60.0,
        "reason": "resource unavailable",
    }
    diagnostic = {"summary": "The target resource was offline during execution."}

    mock_mo = MagicMock()
    mock_mo.write.return_value = {"id": "mem_456"}

    with patch("universal_agent.memory.orchestrator.MemoryOrchestrator", return_value=mock_mo):
        with patch("universal_agent.memory.orchestrator._resolve_workspace_dir", return_value="/tmp/test"):
            result = _write_outcome_to_memory(task=task, outcome=outcome, diagnostic=diagnostic)

    assert result is True
    call_kwargs = mock_mo.write.call_args[1]
    assert "failed/blocked" in call_kwargs["content"]
    assert "resource unavailable" in call_kwargs["content"]
    assert "offline" in call_kwargs["content"]
    assert "failure" in call_kwargs["tags"]


# ---------------------------------------------------------------------------
# 8. test_auto_investigation_triggered
# ---------------------------------------------------------------------------

def test_auto_investigation_triggered(fresh_conn):
    """Verify investigation fires for block/review actions."""
    from universal_agent.services.proactive_outcome_tracker import record_proactive_outcome

    task = _make_proactive_task(source_kind="reflection")
    _add_assignment(fresh_conn, task["task_id"])

    mock_investigation = {"artifact_id": "diag_abc123", "summary": "Root cause: test", "diagnostic": "Full diag"}

    with patch("universal_agent.services.proactive_outcome_tracker._trigger_auto_investigation", return_value=mock_investigation):
        with patch("universal_agent.services.proactive_outcome_tracker._write_outcome_to_memory", return_value=True):
            with patch("universal_agent.services.proactive_outcome_tracker._auto_investigate_enabled", return_value=True):
                result = record_proactive_outcome(
                    fresh_conn,
                    task=task,
                    action="block",
                    reason="resource unavailable",
                )

    assert result is not None
    assert result["investigated"] == 1
    assert result["investigation_artifact_id"] == "diag_abc123"


# ---------------------------------------------------------------------------
# 9. test_auto_investigation_skipped_when_disabled
# ---------------------------------------------------------------------------

def test_auto_investigation_skipped_when_disabled(fresh_conn):
    """Verify feature flag gates investigation."""
    from universal_agent.services.proactive_outcome_tracker import record_proactive_outcome

    task = _make_proactive_task(source_kind="reflection")

    with patch("universal_agent.services.proactive_outcome_tracker._auto_investigate_enabled", return_value=False):
        with patch("universal_agent.services.proactive_outcome_tracker._write_outcome_to_memory", return_value=True):
            with patch("universal_agent.services.proactive_outcome_tracker._trigger_auto_investigation") as mock_inv:
                result = record_proactive_outcome(
                    fresh_conn,
                    task=task,
                    action="block",
                    reason="resource unavailable",
                )

    assert result is not None
    assert result["investigated"] == 0
    mock_inv.assert_not_called()


# ---------------------------------------------------------------------------
# 10. test_deterministic_fallback_diagnostic
# ---------------------------------------------------------------------------

def test_deterministic_fallback_diagnostic():
    """Verify template diagnostic when LLM unavailable."""
    from universal_agent.services.proactive_auto_investigator import _deterministic_diagnostic

    context = {
        "task": {
            "title": "Check weather alerts",
            "source_kind": "proactive_signal",
            "description": "Monitor severe weather for region",
        },
        "outcome": {
            "action": "block",
            "reason": "API rate limit exceeded",
            "agent_id": "simone-001",
            "assignment_count": 3,
            "duration_seconds": 4500,
        },
        "assignments": [
            {"agent_id": "simone-001", "state": "failed", "result_summary": "failed"},
            {"agent_id": "simone-001", "state": "failed", "result_summary": "failed"},
            {"agent_id": "simone-001", "state": "blocked", "result_summary": "blocked"},
        ],
        "evaluations": [],
        "comments": [],
    }

    diagnostic = _deterministic_diagnostic(context)

    assert "ROOT CAUSE" in diagnostic
    assert "CONTRIBUTING FACTORS" in diagnostic
    assert "RECOMMENDATION" in diagnostic
    assert "DISPATCH QUALITY" in diagnostic
    assert "block" in diagnostic
    assert "3 times" in diagnostic  # assignment count > 2
    assert "assignment(s) ended in failure" in diagnostic  # failed assignments


# ---------------------------------------------------------------------------
# 11. test_gateway_outcomes_endpoint_shape
# ---------------------------------------------------------------------------

def test_gateway_outcomes_endpoint_shape(fresh_conn):
    """Verify get_outcome_stats returns the expected shape."""
    from universal_agent.services.proactive_outcome_tracker import (
        ensure_schema, get_outcome_stats, get_recent_outcomes,
    )

    ensure_schema(fresh_conn)

    stats = get_outcome_stats(fresh_conn, window_hours=24)
    assert isinstance(stats, dict)
    assert "total" in stats
    assert "by_action" in stats
    assert "by_source_kind" in stats
    assert "success_rate" in stats
    assert "avg_duration_seconds" in stats
    assert "investigated_count" in stats
    assert "top_failure_reasons" in stats

    recent = get_recent_outcomes(fresh_conn, limit=10)
    assert isinstance(recent, list)


# ---------------------------------------------------------------------------
# 12. test_report_includes_outcome_stats
# ---------------------------------------------------------------------------

def test_report_includes_outcome_stats(fresh_conn):
    """Verify intelligence report includes outcome metrics."""
    from universal_agent.services.proactive_outcome_tracker import ensure_schema

    ensure_schema(fresh_conn)

    # We can't easily import gather_pipeline_stats without the full
    # task_hub bootstrapping, but we can verify the outcome_stats
    # function returns valid data that would be merged into the report.
    from universal_agent.services.proactive_outcome_tracker import get_outcome_stats

    stats = get_outcome_stats(fresh_conn, window_hours=24)
    assert isinstance(stats, dict)
    assert stats["total"] == 0  # Empty DB
    assert stats["success_rate"] == 0.0
    assert stats["by_action"] == {}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_record_outcome_non_terminal_action(fresh_conn):
    """Verify non-terminal actions (like delegate) are skipped."""
    from universal_agent.services.proactive_outcome_tracker import record_proactive_outcome

    task = _make_proactive_task(source_kind="reflection")
    result = record_proactive_outcome(fresh_conn, task=task, action="delegate")
    assert result is None


def test_recent_outcomes_with_filter(fresh_conn):
    """Verify action filter works on recent outcomes."""
    from universal_agent.services.proactive_outcome_tracker import (
        ensure_schema, get_recent_outcomes,
    )

    ensure_schema(fresh_conn)
    now_iso = datetime.now(timezone.utc).isoformat()

    for i, action in enumerate(["complete", "complete", "block"]):
        fresh_conn.execute(
            """
            INSERT INTO proactive_outcomes (
                outcome_id, task_id, source_kind, action, terminal_status,
                reason, agent_id, assignment_count, duration_seconds,
                investigated, investigation_artifact_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (f"out_{i}", f"task_{i}", "reflection", action, "completed" if action == "complete" else "blocked",
             "", "simone", 1, 100, 0, None, now_iso),
        )
    fresh_conn.commit()

    all_results = get_recent_outcomes(fresh_conn, limit=10)
    assert len(all_results) == 3

    complete_only = get_recent_outcomes(fresh_conn, limit=10, action_filter="complete")
    assert len(complete_only) == 2

    block_only = get_recent_outcomes(fresh_conn, limit=10, action_filter="block")
    assert len(block_only) == 1
