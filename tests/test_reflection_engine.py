"""
Test suite for the overnight reflection engine.

Tests verify:
2. Feature flag checks (is_reflection_enabled)
3. Daily budget tracking
4. Context building (prompt generation)
5. Integration with heartbeat guard policy
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import sqlite3
from unittest import mock

import pytest

from universal_agent import task_hub
from universal_agent.services.proactive_budget import (
    DEFAULT_DAILY_BUDGET,
    get_daily_proactive_count,
    has_daily_budget,
    increment_daily_proactive_count,
)
from universal_agent.services.reflection_engine import (
    _format_reflection_prompt,
    _get_open_task_count,
    _get_recent_completions,
    _get_stalled_brainstorms,
    build_reflection_context,
    is_reflection_enabled,
)


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

class TestDailyBudget:

    def test_initial_budget_available(self, db_conn):
        """Fresh DB should have full budget."""
        assert has_daily_budget(db_conn) is True

    def test_budget_increments(self, db_conn):
        """Incrementing should reduce remaining budget."""
        count = increment_daily_proactive_count(db_conn, increment=1)
        assert count == 1
        assert get_daily_proactive_count(db_conn) == 1

    def test_budget_exhaustion(self, db_conn):
        """After max increments, budget should be exhausted."""
        for _ in range(DEFAULT_DAILY_BUDGET):
            increment_daily_proactive_count(db_conn, increment=1)
        assert has_daily_budget(db_conn) is False

    @mock.patch.dict(os.environ, {"UA_PROACTIVE_DAILY_BUDGET": "3"}, clear=False)
    def test_custom_max_tasks(self, db_conn):
        """Custom max should be respected."""
        for _ in range(3):
            increment_daily_proactive_count(db_conn, increment=1)
        assert has_daily_budget(db_conn) is False

    def test_budget_resets_on_new_day(self, db_conn):
        """Budget should reset when the date changes."""
        task_hub._set_setting(db_conn, "proactive_daily_budget_counter", {
            "date": "2020-01-01",  # Old date
            "count": 999,
        })
        # Should return 0 because date doesn't match today
        assert get_daily_proactive_count(db_conn) == 0
        assert has_daily_budget(db_conn) is True


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
        assert "Autonomous Ideation Mode" in text
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
        assert "deploy" in text.lower()
        # Pin the Phase-2a fix: creation goes through task_hub_create, not the
        # lifecycle-only task_hub_task_action.
        assert "task_hub_create" in text


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
        assert "Autonomous Ideation Mode" in ctx["reflection_prompt_text"]


# ===========================================================================
# Guard Policy Integration
# ===========================================================================

class TestGuardPolicyReflection:

    @mock.patch.dict(os.environ, {
        "UA_HEARTBEAT_AUTONOMOUS_ENABLED": "1",
        "UA_REFLECTION_ENABLED": "1",
    }, clear=False)
    def test_reflection_mode_activates_when_enabled(self):
        """Guard policy should enable reflection_mode when queue is empty and reflection is enabled (24/7)."""
        from universal_agent.heartbeat_service import _heartbeat_guard_policy

        policy = _heartbeat_guard_policy(
            actionable_count=0,
            brainstorm_candidate_count=0,
            system_event_count=0,
            has_exec_completion=False,
            pending_question_count=0,
        )
        assert policy["reflection_mode"] is True
        assert policy["skip_reason"] is None  # Should NOT skip

    @mock.patch.dict(os.environ, {
        "UA_HEARTBEAT_AUTONOMOUS_ENABLED": "1",
        "UA_REFLECTION_ENABLED": "1",
    }, clear=False)
    def test_reflection_mode_24x7_always_active_when_enabled(self):
        """Reflection is 24/7 — when enabled, activates regardless of time of day."""
        from universal_agent.heartbeat_service import _heartbeat_guard_policy

        policy = _heartbeat_guard_policy(
            actionable_count=0,
            brainstorm_candidate_count=0,
            system_event_count=0,
            has_exec_completion=False,
            pending_question_count=0,
        )
        # Reflection is 24/7, so with UA_REFLECTION_ENABLED=1 it should activate
        assert policy["reflection_mode"] is True
        assert policy["skip_reason"] is None

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
            pending_question_count=0,
        )
        assert policy["reflection_mode"] is False
        assert policy["skip_reason"] is None  # Still should run (has work)

# ===========================================================================
# _get_open_task_count — perpetual cron rows must not inflate the active count
# ===========================================================================


class TestGetOpenTaskCountExcludesCronRun:
    """Perpetual cron tasks (``source_kind='cron_run'``) sit in ``open`` by
    design between runs (``cron_task_hub_link`` lifecycle). They are
    bookkeeping, not actionable backlog, so they must NOT inflate the
    reflection prompt's "currently queued" count — counting them is what
    seeded the recurring "2 zombie crons never close" misdiagnosis.
    """

    def test_cron_run_open_task_not_counted(self, db_conn):
        from universal_agent.services.cron_task_hub_link import (
            CRON_TASK_SOURCE_KIND,
        )

        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO task_hub_items (task_id, source_kind, title, description, "
            "status, priority, project_key, labels_json, trigger_type, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "cron:paper_to_podcast_daily",
                CRON_TASK_SOURCE_KIND,
                "Cron: paper_to_podcast_daily",
                "perpetual cron row",
                "open",
                5,
                "immediate",
                "[]",
                "manual",
                now,
                now,
            ),
        )
        db_conn.commit()
        # A perpetual cron row in `open` must not count as actionable backlog.
        assert _get_open_task_count(db_conn) == 0

    def test_non_cron_open_task_still_counted(self, db_conn):
        _insert_task(db_conn, title="Real open task", status="open")
        assert _get_open_task_count(db_conn) == 1
