"""
Test suite for the overnight reflection engine.

Tests verify:
1. Time-window logic (is_reflection_hours)
2. Feature flag checks (is_reflection_enabled)
3. Nightly budget tracking
4. Context building (prompt generation)
5. Integration with heartbeat guard policy
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone, timedelta
from unittest import mock

import pytest

from universal_agent.services.reflection_engine import (
    is_reflection_hours,
    is_reflection_enabled,
    has_nightly_budget,
    _increment_nightly_task_count,
    _get_nightly_task_count,
    build_reflection_context,
    _get_recent_completions,
    _get_stalled_brainstorms,
    _get_open_task_count,
    _format_reflection_prompt,
    DEFAULT_MAX_NIGHTLY_TASKS,
    DEFAULT_REFLECTION_START_HOUR,
    DEFAULT_REFLECTION_END_HOUR,
)
from universal_agent import task_hub


@pytest.fixture
def db_conn():
    """In-memory SQLite database with Task Hub schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    yield conn
    conn.close()


def _insert_task(
    conn: sqlite3.Connection,
    *,
    title: str = "Test task",
    status: str = "open",
    project_key: str = "immediate",
    refinement_stage: str | None = None,
    updated_at: str | None = None,
):
    """Helper to insert a task directly into the DB using actual schema columns."""
    now = updated_at or datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO task_hub_items (task_id, source_kind, title, description, status, priority,
            project_key, labels_json, trigger_type, refinement_stage, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"test_{title.replace(' ', '_').lower()}_{now[:10]}",
            "test",
            title,
            f"Description for {title}",
            status,
            5,
            project_key,
            "[]",
            "manual",
            refinement_stage,
            now,
            now,
        ),
    )
    conn.commit()


# ===========================================================================
# is_reflection_hours
# ===========================================================================

class TestIsReflectionHours:

    def test_late_night_default_window(self):
        """11 PM should be within default 10PM-7AM window."""
        now = datetime(2026, 3, 26, 23, 30)
        assert is_reflection_hours(now=now) is True

    def test_midnight_default_window(self):
        """Midnight should be within default window."""
        now = datetime(2026, 3, 27, 0, 0)
        assert is_reflection_hours(now=now) is True

    def test_early_morning_default_window(self):
        """5 AM should be within default window."""
        now = datetime(2026, 3, 27, 5, 0)
        assert is_reflection_hours(now=now) is True

    def test_daytime_outside_window(self):
        """3 PM should be outside default window."""
        now = datetime(2026, 3, 26, 15, 0)
        assert is_reflection_hours(now=now) is False

    def test_boundary_start_hour(self):
        """Exactly 10 PM should be within window."""
        now = datetime(2026, 3, 26, 22, 0)
        assert is_reflection_hours(now=now) is True

    def test_boundary_end_hour(self):
        """Exactly 7 AM should be outside window (it's < 7)."""
        now = datetime(2026, 3, 27, 7, 0)
        assert is_reflection_hours(now=now) is False

    def test_just_before_end(self):
        """6:59 AM should be within window."""
        now = datetime(2026, 3, 27, 6, 59)
        assert is_reflection_hours(now=now) is True

    def test_custom_window(self):
        """Custom window 14-18 should work (no midnight crossing)."""
        now = datetime(2026, 3, 26, 15, 0)
        assert is_reflection_hours(now=now, start_hour=14, end_hour=18) is True
        now = datetime(2026, 3, 26, 19, 0)
        assert is_reflection_hours(now=now, start_hour=14, end_hour=18) is False


# ===========================================================================
# is_reflection_enabled
# ===========================================================================

class TestIsReflectionEnabled:

    @mock.patch.dict(os.environ, {"UA_REFLECTION_ENABLED": "true"}, clear=False)
    def test_explicitly_enabled(self):
        assert is_reflection_enabled() is True

    @mock.patch.dict(os.environ, {"UA_REFLECTION_ENABLED": "false"}, clear=False)
    def test_explicitly_disabled(self):
        assert is_reflection_enabled() is False

    @mock.patch.dict(os.environ, {"UA_REFLECTION_ENABLED": "0"}, clear=False)
    def test_disabled_zero(self):
        assert is_reflection_enabled() is False

    @mock.patch.dict(os.environ, {
        "UA_REFLECTION_ENABLED": "",
        "UA_HEARTBEAT_AUTONOMOUS_ENABLED": "1",
    }, clear=False)
    def test_follows_autonomous_when_unset(self):
        """When not explicitly set, follows UA_HEARTBEAT_AUTONOMOUS_ENABLED."""
        assert is_reflection_enabled() is True

    @mock.patch.dict(os.environ, {
        "UA_REFLECTION_ENABLED": "",
        "UA_HEARTBEAT_AUTONOMOUS_ENABLED": "false",
    }, clear=False)
    def test_follows_autonomous_disabled(self):
        assert is_reflection_enabled() is False


# ===========================================================================
# Nightly Budget
# ===========================================================================

class TestNightlyBudget:

    def test_initial_budget_available(self, db_conn):
        """Fresh DB should have full budget."""
        assert has_nightly_budget(db_conn) is True

    def test_budget_increments(self, db_conn):
        """Incrementing should reduce remaining budget."""
        count = _increment_nightly_task_count(db_conn, increment=1)
        assert count == 1
        assert _get_nightly_task_count(db_conn) == 1

    def test_budget_exhaustion(self, db_conn):
        """After max increments, budget should be exhausted."""
        for _ in range(DEFAULT_MAX_NIGHTLY_TASKS):
            _increment_nightly_task_count(db_conn, increment=1)
        assert has_nightly_budget(db_conn) is False

    @mock.patch.dict(os.environ, {"UA_REFLECTION_MAX_NIGHTLY_TASKS": "3"}, clear=False)
    def test_custom_max_tasks(self, db_conn):
        """Custom max should be respected."""
        for _ in range(3):
            _increment_nightly_task_count(db_conn, increment=1)
        assert has_nightly_budget(db_conn) is False

    def test_budget_resets_on_new_day(self, db_conn):
        """Budget should reset when the date changes."""
        task_hub._set_setting(db_conn, "reflection_nightly_counter", {
            "date": "2020-01-01",  # Old date
            "count": 999,
        })
        # Should return 0 because date doesn't match today
        assert _get_nightly_task_count(db_conn) == 0
        assert has_nightly_budget(db_conn) is True


# ===========================================================================
# Context Queries
# ===========================================================================

class TestContextQueries:

    def test_get_recent_completions_empty(self, db_conn):
        """No completed tasks should return empty list."""
        result = _get_recent_completions(db_conn)
        assert result == []

    def test_get_recent_completions(self, db_conn):
        _insert_task(db_conn, title="Done 1", status="completed")
        _insert_task(db_conn, title="Done 2", status="completed")
        _insert_task(db_conn, title="Open 1", status="open")
        result = _get_recent_completions(db_conn)
        assert len(result) == 2
        titles = {r["title"] for r in result}
        assert "Done 1" in titles
        assert "Done 2" in titles

    def test_get_stalled_brainstorms_empty(self, db_conn):
        result = _get_stalled_brainstorms(db_conn)
        assert result == []

    def test_get_stalled_brainstorms(self, db_conn):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        _insert_task(
            db_conn,
            title="Stalled Brainstorm",
            status="open",
            refinement_stage="brainstorm",
            updated_at=old_time,
        )
        _insert_task(
            db_conn,
            title="Fresh Brainstorm",
            status="open",
            refinement_stage="brainstorm",
        )
        result = _get_stalled_brainstorms(db_conn)
        assert len(result) == 1
        assert result[0]["title"] == "Stalled Brainstorm"

    def test_get_open_task_count(self, db_conn):
        _insert_task(db_conn, title="Open 1", status="open")
        _insert_task(db_conn, title="Open 2", status="in_progress")
        _insert_task(db_conn, title="Parked", status="parked")
        assert _get_open_task_count(db_conn) == 2


# ===========================================================================
# Prompt Formatting
# ===========================================================================

class TestReflectionPrompt:

    def test_empty_prompt(self):
        """Empty context should still produce a valid prompt."""
        text = _format_reflection_prompt(
            recent_completions=[],
            stalled_brainstorms=[],
            open_task_count=0,
            memory_context=[],
            budget_remaining=10,
        )
        assert "Overnight Reflection Mode" in text
        assert "10" in text  # budget

    def test_stalled_brainstorms_section(self):
        text = _format_reflection_prompt(
            recent_completions=[],
            stalled_brainstorms=[
                {"title": "Test Brainstorm", "refinement_stage": "brainstorm", "updated_at": "2026-03-25T12:00"},
            ],
            open_task_count=0,
            memory_context=[],
            budget_remaining=5,
        )
        assert "Stalled Brainstorms" in text
        assert "Test Brainstorm" in text

    def test_safety_guardrails(self):
        """Prompt should include the safety guardrails."""
        text = _format_reflection_prompt(
            recent_completions=[],
            stalled_brainstorms=[],
            open_task_count=0,
            memory_context=[],
            budget_remaining=5,
        )
        assert "Do NOT" in text
        assert "Deploy to production" in text


# ===========================================================================
# Full Context Build
# ===========================================================================

class TestBuildReflectionContext:

    def test_full_context_build(self, db_conn):
        _insert_task(db_conn, title="My completed task", status="completed")
        ctx = build_reflection_context(db_conn, workspace_dir="/tmp")
        assert "recent_completions" in ctx
        assert "stalled_brainstorms" in ctx
        assert "reflection_prompt_text" in ctx
        assert len(ctx["recent_completions"]) == 1
        assert "Overnight Reflection Mode" in ctx["reflection_prompt_text"]


# ===========================================================================
# Guard Policy Integration
# ===========================================================================

class TestGuardPolicyReflection:

    @mock.patch.dict(os.environ, {
        "UA_HEARTBEAT_AUTONOMOUS_ENABLED": "1",
        "UA_REFLECTION_ENABLED": "1",
    }, clear=False)
    def test_reflection_mode_activates_during_night(self):
        """Guard policy should enable reflection_mode when queue is empty at night."""
        from universal_agent.heartbeat_service import _heartbeat_guard_policy

        with mock.patch(
            "universal_agent.services.reflection_engine.is_reflection_hours",
            return_value=True,
        ):
            policy = _heartbeat_guard_policy(
                actionable_count=0,
                brainstorm_candidate_count=0,
                system_event_count=0,
                has_exec_completion=False,
                has_heartbeat_content=False,
                pending_question_count=0,
            )
            assert policy["reflection_mode"] is True
            assert policy["skip_reason"] is None  # Should NOT skip

    @mock.patch.dict(os.environ, {
        "UA_HEARTBEAT_AUTONOMOUS_ENABLED": "1",
        "UA_REFLECTION_ENABLED": "1",
    }, clear=False)
    def test_reflection_mode_off_during_day(self):
        """Guard policy should skip when queue empty during daytime."""
        from universal_agent.heartbeat_service import _heartbeat_guard_policy

        with mock.patch(
            "universal_agent.services.reflection_engine.is_reflection_hours",
            return_value=False,
        ):
            policy = _heartbeat_guard_policy(
                actionable_count=0,
                brainstorm_candidate_count=0,
                system_event_count=0,
                has_exec_completion=False,
                has_heartbeat_content=False,
                pending_question_count=0,
            )
            assert policy["reflection_mode"] is False
            assert policy["skip_reason"] == "no_actionable_work"

    @mock.patch.dict(os.environ, {
        "UA_HEARTBEAT_AUTONOMOUS_ENABLED": "1",
        "UA_REFLECTION_ENABLED": "0",
    }, clear=False)
    def test_reflection_disabled_explicitly(self):
        """Guard policy should skip when reflection explicitly disabled."""
        from universal_agent.heartbeat_service import _heartbeat_guard_policy

        policy = _heartbeat_guard_policy(
            actionable_count=0,
            brainstorm_candidate_count=0,
            system_event_count=0,
            has_exec_completion=False,
            has_heartbeat_content=False,
            pending_question_count=0,
        )
        assert policy["reflection_mode"] is False
        assert policy["skip_reason"] == "no_actionable_work"

    @mock.patch.dict(os.environ, {
        "UA_HEARTBEAT_AUTONOMOUS_ENABLED": "1",
        "UA_REFLECTION_ENABLED": "1",
    }, clear=False)
    def test_normal_work_ignores_reflection(self):
        """When there IS actionable work, reflection_mode should be False."""
        from universal_agent.heartbeat_service import _heartbeat_guard_policy

        policy = _heartbeat_guard_policy(
            actionable_count=3,
            brainstorm_candidate_count=0,
            system_event_count=0,
            has_exec_completion=False,
            has_heartbeat_content=False,
            pending_question_count=0,
        )
        assert policy["reflection_mode"] is False
        assert policy["skip_reason"] is None  # Still should run (has work)
