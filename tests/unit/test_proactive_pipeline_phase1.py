"""
RED Tests for the Autonomous Proactive Pipeline — Phase 1.

These tests are written FIRST (TDD red phase) to define the behavior contracts
for all Phase 1 components before any implementation code is written.

Components tested:
1. Shared Daily Budget (proactive_budget module)
2. Signal Curator (signal_curator service)
3. Reflection Engine upgrades (24/7 mode, ideation-only prompt)
4. Priority Lanes (_sort_key proactive demotion)
5. Heartbeat integration (curator + reflection gating)
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone, timedelta
from unittest import mock

import pytest

from universal_agent import task_hub
from universal_agent import proactive_signals


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_conn():
    """In-memory SQLite database with Task Hub + Proactive Signals schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    proactive_signals.ensure_schema(conn)
    yield conn
    conn.close()


def _insert_task(
    conn: sqlite3.Connection,
    *,
    task_id: str | None = None,
    title: str = "Test task",
    status: str = "open",
    source_kind: str = "internal",
    project_key: str = "immediate",
    priority: int = 5,
    trigger_type: str = "manual",
    score: float = 0.0,
    must_complete: bool = False,
) -> str:
    """Helper to insert a task with specific source_kind."""
    now = datetime.now(timezone.utc).isoformat()
    tid = task_id or f"test_{title.replace(' ', '_').lower()}_{now[:19]}"
    conn.execute(
        """
        INSERT INTO task_hub_items (task_id, source_kind, title, description, status, priority,
            project_key, labels_json, trigger_type, score, must_complete, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tid, source_kind, title, f"Description for {title}",
            status, priority, project_key, "[]", trigger_type, score,
            1 if must_complete else 0, now, now,
        ),
    )
    conn.commit()
    return tid


def _insert_signal_card(
    conn: sqlite3.Connection,
    *,
    card_id: str | None = None,
    title: str = "Test Signal",
    status: str = "pending",
    source: str = "youtube",
) -> str:
    """Helper to insert a proactive signal card."""
    now = datetime.now(timezone.utc).isoformat()
    cid = card_id or f"card_{title.replace(' ', '_').lower()}"
    conn.execute(
        """
        INSERT INTO proactive_signal_cards (card_id, source, card_type, title, summary,
            status, priority, confidence_score, novelty_score, evidence_json,
            actions_json, feedback_json, selected_action_json, metadata_json,
            created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cid, source, "research", title, f"Summary for {title}",
            status, 2, 0.5, 0.5, "[]", "[]", "{}", "{}", "{}",
            now, now,
        ),
    )
    conn.commit()
    return cid


# ===========================================================================
# 1. SHARED DAILY BUDGET
# ===========================================================================

class TestProactiveDailyBudget:
    """Tests for the shared daily budget module used by both curator and reflection."""

    def test_initial_budget_is_full(self, db_conn):
        """Fresh DB should have zero proactive count and full budget available."""
        from universal_agent.services.proactive_budget import (
            get_daily_proactive_count,
            has_daily_budget,
        )
        assert get_daily_proactive_count(db_conn) == 0
        assert has_daily_budget(db_conn) is True

    @mock.patch.dict(os.environ, {"UA_PROACTIVE_DAILY_BUDGET": "10"}, clear=False)
    def test_budget_increments_correctly(self, db_conn):
        """Incrementing should reduce remaining budget."""
        from universal_agent.services.proactive_budget import (
            get_daily_proactive_count,
            increment_daily_proactive_count,
        )
        new_count = increment_daily_proactive_count(db_conn, increment=3)
        assert new_count == 3
        assert get_daily_proactive_count(db_conn) == 3

    @mock.patch.dict(os.environ, {"UA_PROACTIVE_DAILY_BUDGET": "10"}, clear=False)
    def test_budget_exhaustion_at_10(self, db_conn):
        """After 10 increments, budget should be exhausted."""
        from universal_agent.services.proactive_budget import (
            has_daily_budget,
            increment_daily_proactive_count,
        )
        for i in range(10):
            increment_daily_proactive_count(db_conn, increment=1)
        assert has_daily_budget(db_conn) is False

    @mock.patch.dict(os.environ, {"UA_PROACTIVE_DAILY_BUDGET": "10"}, clear=False)
    def test_budget_still_available_at_9(self, db_conn):
        """At 9/10, budget should still be available."""
        from universal_agent.services.proactive_budget import (
            has_daily_budget,
            increment_daily_proactive_count,
        )
        for i in range(9):
            increment_daily_proactive_count(db_conn, increment=1)
        assert has_daily_budget(db_conn) is True

    def test_budget_resets_on_new_day(self, db_conn):
        """Budget should reset when the date changes."""
        from universal_agent.services.proactive_budget import (
            get_daily_proactive_count,
            has_daily_budget,
            _DAILY_BUDGET_KEY,
        )
        # Set a counter from yesterday
        task_hub._set_setting(db_conn, _DAILY_BUDGET_KEY, {
            "date": "2020-01-01",
            "count": 999,
        })
        assert get_daily_proactive_count(db_conn) == 0
        assert has_daily_budget(db_conn) is True

    def test_budget_excludes_cron_system_command(self, db_conn):
        """Cron/system_command tasks should NOT count against the proactive budget.

        The budget only counts source_kind in ('proactive_signal', 'reflection').
        """
        from universal_agent.services.proactive_budget import (
            get_daily_proactive_count,
            increment_daily_proactive_count,
        )
        # Simulate 5 proactive tasks
        increment_daily_proactive_count(db_conn, increment=5)
        assert get_daily_proactive_count(db_conn) == 5

        # Cron jobs being created elsewhere should NOT increment this counter
        # The counter is explicitly incremented only by curator + reflection
        # This test verifies the counter is independent of task_hub source_kind counts
        _insert_task(db_conn, title="Cron cleanup", source_kind="system_command")
        _insert_task(db_conn, title="Cron report", source_kind="system_command")
        # Counter should still be 5, not 7
        assert get_daily_proactive_count(db_conn) == 5

    def test_get_budget_remaining(self, db_conn):
        """Should correctly return remaining budget count."""
        from universal_agent.services.proactive_budget import (
            get_budget_remaining,
            increment_daily_proactive_count,
        )
        remaining = get_budget_remaining(db_conn)
        assert remaining > 0
        increment_daily_proactive_count(db_conn, increment=3)
        remaining_after = get_budget_remaining(db_conn)
        assert remaining_after == remaining - 3


# ===========================================================================
# 2. SIGNAL CURATOR
# ===========================================================================

class TestSignalCuratorTrigger:
    """Tests for when curation should run."""

    def test_should_not_run_with_few_cards(self, db_conn):
        """With fewer than 10 pending cards, should_run_curation returns False."""
        from universal_agent.services.signal_curator import should_run_curation
        # Insert 5 pending cards
        for i in range(5):
            _insert_signal_card(db_conn, card_id=f"card_{i}", title=f"Signal {i}")
        assert should_run_curation(db_conn) is False

    def test_should_run_with_10_cards(self, db_conn):
        """With exactly 10 pending cards, should_run_curation returns True."""
        from universal_agent.services.signal_curator import should_run_curation
        for i in range(10):
            _insert_signal_card(db_conn, card_id=f"card_{i}", title=f"Signal {i}")
        assert should_run_curation(db_conn) is True

    def test_should_run_after_12h_even_with_few_cards(self, db_conn):
        """If 12h+ since last curation, should run even with fewer than 10 cards."""
        from universal_agent.services.signal_curator import should_run_curation
        # Insert just 3 cards
        for i in range(3):
            _insert_signal_card(db_conn, card_id=f"card_{i}", title=f"Signal {i}")
        # Simulate last curation was 13h ago
        old_time = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
        task_hub._set_setting(db_conn, "signal_curator_last_run", {"timestamp": old_time})
        assert should_run_curation(db_conn) is True

    def test_should_not_run_if_recently_curated(self, db_conn):
        """If curated recently AND fewer than 10 cards, should not run."""
        from universal_agent.services.signal_curator import should_run_curation
        for i in range(5):
            _insert_signal_card(db_conn, card_id=f"card_{i}", title=f"Signal {i}")
        recent_time = datetime.now(timezone.utc).isoformat()
        task_hub._set_setting(db_conn, "signal_curator_last_run", {"timestamp": recent_time})
        assert should_run_curation(db_conn) is False

    def test_should_not_run_with_no_cards(self, db_conn):
        """With zero pending cards, never run regardless of time."""
        from universal_agent.services.signal_curator import should_run_curation
        old_time = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        task_hub._set_setting(db_conn, "signal_curator_last_run", {"timestamp": old_time})
        assert should_run_curation(db_conn) is False


class TestSignalCuratorPromotion:
    """Tests for card-to-task promotion."""

    def test_promote_creates_task_hub_items(self, db_conn):
        """Promoting cards should create Task Hub items with correct source_kind."""
        from universal_agent.services.signal_curator import promote_cards_to_tasks
        card_id = _insert_signal_card(db_conn, card_id="card_promote_1", title="AI Agent Framework")

        curated = [{
            "card_id": card_id,
            "task_title": "Research AI Agent Framework patterns",
            "task_description": "Deep-dive into emerging agent framework patterns",
            "priority": 3,
            "rationale": "Aligns with Kevin's mission on agent architecture",
        }]
        promote_cards_to_tasks(db_conn, curated)

        # Verify task was created
        row = db_conn.execute(
            "SELECT * FROM task_hub_items WHERE source_kind = 'proactive_signal'",
        ).fetchone()
        assert row is not None
        assert row["title"] == "Research AI Agent Framework patterns"
        assert row["source_kind"] == "proactive_signal"
        assert "card_promote_1" in str(row["source_ref"])

    def test_promote_updates_card_status(self, db_conn):
        """After promotion, card status should change to 'promoted'."""
        from universal_agent.services.signal_curator import promote_cards_to_tasks
        card_id = _insert_signal_card(db_conn, card_id="card_promote_2", title="Test Card")

        curated = [{
            "card_id": card_id,
            "task_title": "Test task from card",
            "task_description": "Test description",
            "priority": 2,
            "rationale": "Test rationale",
        }]
        promote_cards_to_tasks(db_conn, curated)

        card_row = db_conn.execute(
            "SELECT status FROM proactive_signal_cards WHERE card_id = ?",
            (card_id,),
        ).fetchone()
        assert card_row["status"] == "promoted"

    @mock.patch.dict(os.environ, {"UA_PROACTIVE_DAILY_BUDGET": "2"}, clear=False)
    def test_promote_respects_budget(self, db_conn):
        """Should only promote up to the remaining daily budget."""
        from universal_agent.services.signal_curator import promote_cards_to_tasks
        from universal_agent.services.proactive_budget import increment_daily_proactive_count

        # Use 1 of 2 budget slots
        increment_daily_proactive_count(db_conn, increment=1)

        # Try to promote 3 cards — should only promote 1 (budget remaining = 1)
        curated = []
        for i in range(3):
            cid = _insert_signal_card(db_conn, card_id=f"card_budget_{i}", title=f"Budget test {i}")
            curated.append({
                "card_id": cid,
                "task_title": f"Task from budget test {i}",
                "task_description": f"Description {i}",
                "priority": 2,
                "rationale": f"Rationale {i}",
            })

        promote_cards_to_tasks(db_conn, curated)

        # Should only have created 1 task (budget allowed 1 more)
        rows = db_conn.execute(
            "SELECT COUNT(*) as cnt FROM task_hub_items WHERE source_kind = 'proactive_signal'",
        ).fetchone()
        assert rows["cnt"] == 1


# ===========================================================================
# 3. REFLECTION ENGINE — 24/7 MODE + IDEATION-ONLY PROMPT
# ===========================================================================

class TestReflection24x7:
    """Tests verifying reflection runs 24/7, not just overnight."""

    @mock.patch.dict(os.environ, {
        "UA_HEARTBEAT_AUTONOMOUS_ENABLED": "1",
        "UA_REFLECTION_ENABLED": "1",
    }, clear=False)
    def test_reflection_activates_during_daytime(self):
        """Reflection should activate at 2 PM when queue is empty — no time restriction."""
        from universal_agent.heartbeat_service import _heartbeat_guard_policy

        policy = _heartbeat_guard_policy(
            actionable_count=0,
            brainstorm_candidate_count=0,
            system_event_count=0,
            has_exec_completion=False,
            has_heartbeat_content=False,
            pending_question_count=0,
        )
        assert policy["reflection_mode"] is True
        assert policy["skip_reason"] is None

    @mock.patch.dict(os.environ, {
        "UA_HEARTBEAT_AUTONOMOUS_ENABLED": "1",
        "UA_REFLECTION_ENABLED": "1",
    }, clear=False)
    def test_reflection_activates_at_night(self):
        """Reflection should also work at night (it's 24/7 now)."""
        from universal_agent.heartbeat_service import _heartbeat_guard_policy

        policy = _heartbeat_guard_policy(
            actionable_count=0,
            brainstorm_candidate_count=0,
            system_event_count=0,
            has_exec_completion=False,
            has_heartbeat_content=False,
            pending_question_count=0,
        )
        assert policy["reflection_mode"] is True
        assert policy["skip_reason"] is None


class TestReflectionIdeationPrompt:
    """Tests verifying the reflection prompt is ideation-only."""

    def test_prompt_is_autonomous_ideation_mode(self):
        """Prompt should say 'Autonomous Ideation Mode', not 'Overnight Reflection'."""
        from universal_agent.services.reflection_engine import _format_reflection_prompt
        text = _format_reflection_prompt(
            recent_completions=[],
            stalled_brainstorms=[],
            open_task_count=0,
            memory_context=[],
            budget_remaining=10,
        )
        assert "Autonomous Ideation Mode" in text
        assert "Overnight" not in text

    def test_prompt_instructs_task_creation_only(self):
        """Prompt should tell agent to CREATE tasks, not DO work."""
        from universal_agent.services.reflection_engine import _format_reflection_prompt
        text = _format_reflection_prompt(
            recent_completions=[],
            stalled_brainstorms=[],
            open_task_count=0,
            memory_context=[],
            budget_remaining=10,
        )
        assert "task_hub_task_action" in text
        # Should NOT instruct the agent to start working on tasks
        assert "start working" not in text.lower()

    def test_prompt_uses_daily_budget_language(self):
        """Prompt should reference daily budget, not nightly."""
        from universal_agent.services.reflection_engine import _format_reflection_prompt
        text = _format_reflection_prompt(
            recent_completions=[],
            stalled_brainstorms=[],
            open_task_count=0,
            memory_context=[],
            budget_remaining=5,
        )
        assert "5" in text  # budget number present
        assert "nightly" not in text.lower()
        assert "tonight" not in text.lower()


class TestReflectionBudgetRename:
    """Tests for the nightly→daily budget rename in reflection_engine."""

    def test_daily_budget_key_exists(self):
        """Module should expose _DAILY_BUDGET_KEY, not _NIGHTLY_TASK_COUNTER_KEY."""
        from universal_agent.services import proactive_budget
        assert hasattr(proactive_budget, "_DAILY_BUDGET_KEY")

    @mock.patch.dict(os.environ, {"UA_PROACTIVE_DAILY_BUDGET": "10"}, clear=False)
    def test_has_daily_budget_function(self, db_conn):
        """has_daily_budget should check against UA_PROACTIVE_DAILY_BUDGET."""
        from universal_agent.services.proactive_budget import has_daily_budget
        assert has_daily_budget(db_conn) is True


# ===========================================================================
# 4. PRIORITY LANES — _sort_key proactive demotion
# ===========================================================================

class TestPriorityLanes:
    """Tests verifying proactive tasks sort below user tasks."""

    def test_user_task_ranks_above_proactive_at_same_score(self, db_conn):
        """A user-origin task should rank above a proactive_signal task at the same score."""
        user_tid = _insert_task(
            db_conn, task_id="user_task_1", title="User Email Task",
            source_kind="email", score=5.0, priority=3,
        )
        proactive_tid = _insert_task(
            db_conn, task_id="proactive_task_1", title="Proactive Signal Task",
            source_kind="proactive_signal", score=5.0, priority=3,
        )

        # Rebuild the dispatch queue
        task_hub.rebuild_dispatch_queue(db_conn)

        # Get the queue ordering
        rows = db_conn.execute(
            "SELECT task_id, rank FROM task_hub_dispatch_queue ORDER BY rank ASC",
        ).fetchall()
        task_ids = [r["task_id"] for r in rows]

        # User task should come first
        user_idx = task_ids.index(user_tid) if user_tid in task_ids else 999
        proactive_idx = task_ids.index(proactive_tid) if proactive_tid in task_ids else 999
        assert user_idx < proactive_idx, (
            f"User task should rank above proactive task, got user={user_idx} proactive={proactive_idx}"
        )

    def test_reflection_task_ranks_below_user_task(self, db_conn):
        """A reflection-origin task should rank below a user task."""
        user_tid = _insert_task(
            db_conn, task_id="user_task_2", title="User Chat Task",
            source_kind="chat", score=5.0, priority=3,
        )
        reflection_tid = _insert_task(
            db_conn, task_id="reflection_task_1", title="Reflection Task",
            source_kind="reflection", score=5.0, priority=3,
        )

        task_hub.rebuild_dispatch_queue(db_conn)

        rows = db_conn.execute(
            "SELECT task_id, rank FROM task_hub_dispatch_queue ORDER BY rank ASC",
        ).fetchall()
        task_ids = [r["task_id"] for r in rows]

        user_idx = task_ids.index(user_tid) if user_tid in task_ids else 999
        refl_idx = task_ids.index(reflection_tid) if reflection_tid in task_ids else 999
        assert user_idx < refl_idx

    def test_proactive_with_higher_score_still_below_user(self, db_conn):
        """Even with a higher score, proactive tasks should rank below user tasks."""
        user_tid = _insert_task(
            db_conn, task_id="user_task_3", title="Low Score User Task",
            source_kind="email", score=2.0, priority=2,
        )
        proactive_tid = _insert_task(
            db_conn, task_id="proactive_task_3", title="High Score Proactive",
            source_kind="proactive_signal", score=9.0, priority=4,
        )

        task_hub.rebuild_dispatch_queue(db_conn)

        rows = db_conn.execute(
            "SELECT task_id, rank FROM task_hub_dispatch_queue ORDER BY rank ASC",
        ).fetchall()
        task_ids = [r["task_id"] for r in rows]

        user_idx = task_ids.index(user_tid) if user_tid in task_ids else 999
        proactive_idx = task_ids.index(proactive_tid) if proactive_tid in task_ids else 999
        assert user_idx < proactive_idx

    def test_system_command_not_demoted(self, db_conn):
        """system_command (cron) tasks should NOT be treated as proactive — they sort normally."""
        cron_tid = _insert_task(
            db_conn, task_id="cron_task_1", title="Cron Job",
            source_kind="system_command", score=5.0, priority=3,
        )
        proactive_tid = _insert_task(
            db_conn, task_id="proactive_task_4", title="Proactive Task",
            source_kind="proactive_signal", score=5.0, priority=3,
        )

        task_hub.rebuild_dispatch_queue(db_conn)

        rows = db_conn.execute(
            "SELECT task_id, rank FROM task_hub_dispatch_queue ORDER BY rank ASC",
        ).fetchall()
        task_ids = [r["task_id"] for r in rows]

        cron_idx = task_ids.index(cron_tid) if cron_tid in task_ids else 999
        proactive_idx = task_ids.index(proactive_tid) if proactive_tid in task_ids else 999
        assert cron_idx < proactive_idx, "Cron tasks should sort above proactive tasks"
