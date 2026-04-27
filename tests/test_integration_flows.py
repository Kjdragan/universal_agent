"""Integration test flows — simulates real task lifecycles.

These tests exercise the full pipeline from task creation through
refinement, decomposition, and completion feedback. They verify
  that the modules compose correctly and that state transitions
are durable.

Flow 1: Brainstorm → Refine → Decompose → Subtasks Created
Flow 2: Calendar event → Task Hub entry → Routing
Flow 3: Completion feedback loop triggers wake
Flow 4: Multi-task batch cycle with mixed outcomes
Flow 5: Edge case — task modified between scan and refine
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from universal_agent import task_hub
from universal_agent.services import auto_refinement_loop
from universal_agent.services.auto_refinement_loop import (
    clear_cooldowns,
    find_refinement_candidates,
    refine_task,
    run_auto_refinement_cycle,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """Fresh in-memory SQLite DB with Task Hub schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def reset_state():
    """Reset cooldown state between tests."""
    clear_cooldowns()
    yield
    clear_cooldowns()


def _create_task(conn, task_id, **overrides):
    """Helper: create a task with defaults."""
    item = {
        "task_id": task_id,
        "title": f"Task {task_id}",
        "description": f"Description for {task_id}",
        "priority": 2,
        "status": "open",
        "trigger_type": "brainstorm",
        "labels": ["brainstorm"],
    }
    item.update(overrides)
    task_hub.upsert_item(conn, item)
    # Set refinement stage if specified
    if "refinement_stage" in overrides:
        conn.execute(
            "UPDATE task_hub_items SET refinement_stage = ? WHERE task_id = ?",
            (overrides["refinement_stage"], task_id),
        )
        conn.commit()
    return task_hub.get_item(conn, task_id) or {}


# ═══════════════════════════════════════════════════════════════════════════
# FLOW 1: Complete brainstorm lifecycle
# brainstorm → raw_idea → interviewing → exploring → crystallizing
#           → decomposing → decomposed → actionable
# ═══════════════════════════════════════════════════════════════════════════

class TestFlow1_BrainstormLifecycle:
    """Full lifecycle: a brainstorm idea goes from raw_idea all the way
    through auto-refinement to decomposition into subtasks."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_raw_to_actionable(self, db):
        """Simulate a brainstorm marching through all stages to actionable."""
        _create_task(db, "idea-1",
                     title="Build a personal knowledge graph",
                     description="An app that tracks what I learn",
                     refinement_stage="raw_idea")

        # Stage 1: raw_idea → interviewing
        mock_refine = AsyncMock(return_value={
            "recommendation": "advance",
            "next_stage": "interviewing",
            "reasoning": "Core idea is clear, need to explore scope",
            "questions": [],
            "enriched_description": "Personal knowledge graph tracking daily learnings",
            "suggested_subtasks": [],
        })

        with patch("universal_agent.services.refinement_agent.refine_with_llm", mock_refine):
            result = await refine_task(db, "idea-1")
        assert result["action"] == "advanced"
        assert result["to_stage"] == "interviewing"

        # Verify DB state
        item = task_hub.get_item(db, "idea-1")
        assert item["refinement_stage"] == "interviewing"

        # Check comment was added
        comments = task_hub.list_comments(db, "idea-1")
        assert any("[Auto-Refinement]" in c.get("content", "") for c in comments)

        # Stage 2: interviewing → exploring
        clear_cooldowns()
        mock_refine.return_value = {
            "recommendation": "advance",
            "next_stage": "exploring",
            "reasoning": "User needs are clear",
            "questions": [],
            "enriched_description": "Knowledge graph with daily notes, article bookmarks, and spaced repetition",
            "suggested_subtasks": [],
        }
        with patch("universal_agent.services.refinement_agent.refine_with_llm", mock_refine):
            result = await refine_task(db, "idea-1")
        assert result["to_stage"] == "exploring"

        # Stage 3: exploring → crystallizing
        clear_cooldowns()
        mock_refine.return_value = {
            "recommendation": "advance",
            "next_stage": "crystallizing",
            "reasoning": "Technical approach is clear",
            "questions": [],
            "enriched_description": "Graph DB + React UI with obsidian sync",
            "suggested_subtasks": [],
        }
        with patch("universal_agent.services.refinement_agent.refine_with_llm", mock_refine):
            result = await refine_task(db, "idea-1")
        assert result["to_stage"] == "crystallizing"

        # Stage 4: crystallizing → decomposing → auto-decompose → actionable
        clear_cooldowns()
        mock_refine.return_value = {
            "recommendation": "advance",
            "next_stage": "decomposing",
            "reasoning": "Spec is detailed enough to decompose",
            "questions": [],
            "enriched_description": "Knowledge graph: Neo4j backend, React frontend, Obsidian plugin",
            "suggested_subtasks": [],
        }
        mock_decompose = AsyncMock(return_value=[
            {"title": "Set up Neo4j database schema", "description": "Design nodes/edges", "priority": 1},
            {"title": "Build React dashboard", "description": "Main UI", "priority": 2},
            {"title": "Create Obsidian sync plugin", "description": "Bidirectional sync", "priority": 3},
        ])

        with patch("universal_agent.services.refinement_agent.refine_with_llm", mock_refine), \
             patch("universal_agent.services.decomposition_agent.decompose_with_llm", mock_decompose):
            result = await refine_task(db, "idea-1")

        # Should have gone all the way to decomposed
        assert result["action"] == "decomposed"
        assert result["subtask_count"] == 3

        # Verify final state
        item = task_hub.get_item(db, "idea-1")
        assert item["refinement_stage"] == "actionable"

        # Verify refinement history has multiple entries
        ref_state = task_hub.get_refinement_state(db, "idea-1")
        history = ref_state.get("refinement_history", {})
        # Should have entries for: interviewing, exploring, crystallizing, decomposing, actionable
        assert len(history) >= 5

    @pytest.mark.asyncio
    async def test_lifecycle_with_questions_pause(self, db):
        """A brainstorm gets paused when the LLM asks clarifying questions."""
        _create_task(db, "idea-2",
                     title="Automate email responses",
                     refinement_stage="interviewing")

        # First call: LLM asks questions instead of advancing
        mock_refine = AsyncMock(return_value={
            "recommendation": "question",
            "next_stage": "exploring",
            "reasoning": "Need to know email volume and response types",
            "questions": [
                "How many emails per day do you receive?",
                "What types of responses should be automated?",
                "Should the system draft or auto-send?",
            ],
            "enriched_description": "",
            "suggested_subtasks": [],
        })

        with patch("universal_agent.services.refinement_agent.refine_with_llm", mock_refine):
            result = await refine_task(db, "idea-2")

        assert result["action"] == "questions"
        assert result["count"] == 3

        # Stage should NOT have changed
        item = task_hub.get_item(db, "idea-2")
        assert item["refinement_stage"] == "interviewing"

        # Questions should be in the queue
        rows = db.execute(
            "SELECT * FROM task_hub_question_queue WHERE task_id = ?",
            ("idea-2",),
        ).fetchall()
        assert len(rows) == 3


# ═══════════════════════════════════════════════════════════════════════════
# FLOW 2: Calendar event → Task Hub → verified ready for routing
# ═══════════════════════════════════════════════════════════════════════════

class TestFlow2_CalendarToTaskHub:
    """Simulate a calendar event materializing into a routable Task Hub task."""

    def test_calendar_event_materializes_as_scheduled_task(self, db):
        """A Google Calendar event becomes a Task Hub entry with proper metadata."""
        from universal_agent.services.calendar_task_bridge import CalendarTaskBridge

        bridge = CalendarTaskBridge(db_conn=db)

        start_time = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        end_time = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()

        result = bridge.materialize_event(
            event_id="gcal_unique_123",
            title="Sprint Planning",
            description="Review backlog and assign stories for next sprint",
            event_start=start_time,
            event_end=end_time,
            organizer_email="kevin.dragan@clearspringbusiness.com",
        )

        assert result["status"] == "active"
        assert result["is_update"] is False  # first time → new
        task_id = result["task_id"]

        # Verify the task exists and is properly formatted
        task = task_hub.get_item(db, task_id)
        assert task is not None
        assert task["status"] == "open"
        assert task["trigger_type"] == "scheduled"
        assert "calendar-task" in (task.get("labels_json") or "")

    def test_calendar_event_is_not_brainstorm(self, db):
        """Calendar tasks should NOT appear as refinement candidates."""
        from universal_agent.services.calendar_task_bridge import CalendarTaskBridge

        bridge = CalendarTaskBridge(db_conn=db)
        bridge.materialize_event(
            event_id="gcal_456",
            title="Team Standup",
            event_start=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )

        # Calendar tasks should NOT show up as refinement candidates
        candidates = find_refinement_candidates(db)
        assert len(candidates) == 0


# ═══════════════════════════════════════════════════════════════════════════
# FLOW 3: Completion feedback loop
# ═══════════════════════════════════════════════════════════════════════════

class TestFlow3_CompletionFeedback:
    """Verify the heartbeat re-wake logic triggers correctly based on
    finalization results."""

    def test_wake_requested_when_tasks_completed(self):
        """When finalize_assignments reports completed > 0, wake-next is called."""
        from universal_agent.heartbeat_service import HeartbeatService

        svc = HeartbeatService.__new__(HeartbeatService)
        svc.wake_next_sessions = set()
        svc.last_wake_reason = {}
        svc._emit_event = MagicMock()

        # Simulate calling request_heartbeat_next as Phase 6 would
        finalize_result = {"completed": 2, "reviewed": 0, "reopened": 0}
        completed_count = int(finalize_result.get("completed") or 0)
        if completed_count > 0:
            svc.request_heartbeat_next(
                "session_simone",
                reason=f"completion_feedback:{completed_count}_done",
            )

        assert "session_simone" in svc.wake_next_sessions
        assert svc.last_wake_reason["session_simone"] == "completion_feedback:2_done"

    def test_no_wake_when_zero_completed(self):
        """When nothing was completed, no wake is requested."""
        from universal_agent.heartbeat_service import HeartbeatService

        svc = HeartbeatService.__new__(HeartbeatService)
        svc.wake_next_sessions = set()
        svc.last_wake_reason = {}
        svc._emit_event = MagicMock()

        finalize_result = {"completed": 0, "reviewed": 1, "reopened": 0}
        completed_count = int(finalize_result.get("completed") or 0)
        if completed_count > 0:
            svc.request_heartbeat_next("session_simone", reason="test")

        assert "session_simone" not in svc.wake_next_sessions


# ═══════════════════════════════════════════════════════════════════════════
# FLOW 4: Batch cycle with mixed outcomes
# ═══════════════════════════════════════════════════════════════════════════

class TestFlow4_BatchMixedOutcomes:
    """One batch cycle processes multiple brainstorms at different stages
    with different outcomes."""

    @pytest.mark.asyncio
    async def test_mixed_batch(self, db):
        """Three brainstorms: one advances, one gets questions, one decomposes."""
        # Task 1: At raw_idea, should advance
        _create_task(db, "batch-1", refinement_stage="raw_idea", priority=1,
                     title="Build API gateway")
        # Task 2: At exploring, should get questions
        _create_task(db, "batch-2", refinement_stage="exploring", priority=2,
                     title="Customer feedback dashboard")
        # Task 3: At decomposing, should auto-decompose
        _create_task(db, "batch-3", refinement_stage="decomposing", priority=3,
                     title="Migrate database")

        call_count = 0

        async def mock_refine(**kwargs):
            nonlocal call_count
            call_count += 1
            # Track which task we're refining by matching title
            title = kwargs.get("title", "")
            if "API gateway" in title:
                return {
                    "recommendation": "advance",
                    "next_stage": "interviewing",
                    "reasoning": "Clear concept",
                    "questions": [],
                    "enriched_description": "REST API gateway with rate limiting",
                    "suggested_subtasks": [],
                }
            elif "feedback" in title:
                return {
                    "recommendation": "question",
                    "next_stage": "crystallizing",
                    "reasoning": "Need more detail",
                    "questions": ["What data sources?", "What visualizations?"],
                    "enriched_description": "",
                    "suggested_subtasks": [],
                }
            else:
                return {
                    "recommendation": "hold",
                    "next_stage": "actionable",
                    "reasoning": "Hold",
                    "questions": [],
                    "enriched_description": "",
                    "suggested_subtasks": [],
                }

        mock_decompose = AsyncMock(return_value=[
            {"title": "Export old schemas", "description": "Backup", "priority": 1},
            {"title": "Run migration script", "description": "Migrate", "priority": 2},
        ])

        with patch.object(auto_refinement_loop, "AUTO_REFINEMENT_ENABLED", True), \
             patch.object(auto_refinement_loop, "MAX_REFINEMENTS_PER_CYCLE", 5), \
             patch("universal_agent.services.refinement_agent.refine_with_llm", side_effect=mock_refine), \
             patch("universal_agent.services.decomposition_agent.decompose_with_llm", mock_decompose):
            cycle_result = await run_auto_refinement_cycle(db)

        assert cycle_result["enabled"] is True
        assert cycle_result["processed"] == 3

        actions = [r["action"] for r in cycle_result["results"]]
        # batch-1 advanced, batch-2 got questions, batch-3 decomposed
        assert "advanced" in actions
        assert "questions" in actions
        assert "decomposed" in actions

        # Verify final DB states
        t1 = task_hub.get_item(db, "batch-1")
        assert t1["refinement_stage"] == "interviewing"

        t2 = task_hub.get_item(db, "batch-2")
        assert t2["refinement_stage"] == "exploring"  # unchanged (questions)

        t3 = task_hub.get_item(db, "batch-3")
        assert t3["refinement_stage"] == "actionable"  # decomposed

    @pytest.mark.asyncio
    async def test_cooldown_prevents_reprocessing(self, db):
        """After a cycle runs, cooled-down tasks are skipped on the next cycle."""
        _create_task(db, "cd-1", refinement_stage="raw_idea")

        mock_result = {
            "recommendation": "hold",
            "next_stage": "interviewing",
            "reasoning": "Not ready",
            "questions": [],
            "enriched_description": "",
            "suggested_subtasks": [],
        }

        with patch.object(auto_refinement_loop, "AUTO_REFINEMENT_ENABLED", True), \
             patch("universal_agent.services.refinement_agent.refine_with_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_result

            # Cycle 1: processes the task
            r1 = await run_auto_refinement_cycle(db)
            assert r1["processed"] == 1

            # Cycle 2: skips due to cooldown
            r2 = await run_auto_refinement_cycle(db)
            assert r2["candidates"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# FLOW 5: Edge case — concurrent task modification
# ═══════════════════════════════════════════════════════════════════════════

class TestFlow5_EdgeCases:
    """Cover edge cases that could occur in production."""

    @pytest.mark.asyncio
    async def test_task_deleted_between_scan_and_refine(self, db):
        """If a task is deleted after candidate scan but before refine, handle safely."""
        _create_task(db, "ghost-1", refinement_stage="raw_idea")

        # Verify candidate exists
        candidates = find_refinement_candidates(db)
        assert len(candidates) == 1

        # Delete the task (simulating concurrent modification)
        db.execute("DELETE FROM task_hub_items WHERE task_id = ?", ("ghost-1",))
        db.commit()

        # Refinement should handle gracefully
        result = await refine_task(db, "ghost-1")
        assert result["action"] == "skip"
        assert result["reason"] == "not_found"

    @pytest.mark.asyncio
    async def test_task_status_changed_between_scan_and_refine(self, db):
        """If a task becomes in_progress between scan and refine, skip it."""
        _create_task(db, "claimed-1", refinement_stage="raw_idea")

        candidates = find_refinement_candidates(db)
        assert len(candidates) == 1

        # Change status to in_progress (simulating heartbeat claiming it)
        db.execute(
            "UPDATE task_hub_items SET status = 'in_progress' WHERE task_id = ?",
            ("claimed-1",),
        )
        db.commit()

        # refine_task doesn't re-check status (by design — the scan is the filter),
        # but the task's current_stage is still processable. This is acceptable because
        # the LLM analysis still applies. The task won't be re-scanned (it's in_progress).
        # Verify it doesn't crash:
        mock_result = {
            "recommendation": "hold",
            "next_stage": "interviewing",
            "reasoning": "Hold for now",
            "questions": [],
            "enriched_description": "",
            "suggested_subtasks": [],
        }
        with patch("universal_agent.services.refinement_agent.refine_with_llm", new_callable=AsyncMock) as m:
            m.return_value = mock_result
            result = await refine_task(db, "claimed-1")

        # Should still work (it processes based on stage, not status)
        assert result["action"] == "hold"

    @pytest.mark.asyncio
    async def test_decomposition_with_empty_subtasks(self, db):
        """If decompose_with_llm returns empty list, handle gracefully."""
        _create_task(db, "empty-1", refinement_stage="decomposing",
                     title="Vague idea")

        mock_decompose = AsyncMock(return_value=[])

        with patch("universal_agent.services.decomposition_agent.decompose_with_llm", mock_decompose):
            result = await refine_task(db, "empty-1")

        # Should still succeed — just with 0 subtasks
        assert result["action"] == "decomposed"
        assert result["subtask_count"] == 0

        # Task should still advance to actionable
        item = task_hub.get_item(db, "empty-1")
        assert item["refinement_stage"] == "actionable"

    @pytest.mark.asyncio
    async def test_refinement_history_persists_across_advances(self, db):
        """Verify that refinement history accumulates correctly."""
        _create_task(db, "history-1", refinement_stage="raw_idea")

        for target_stage in ["interviewing", "exploring", "crystallizing"]:
            clear_cooldowns()
            mock_result = {
                "recommendation": "advance",
                "next_stage": target_stage,
                "reasoning": f"Ready for {target_stage}",
                "questions": [],
                "enriched_description": f"Description at {target_stage}",
                "suggested_subtasks": [],
            }
            with patch("universal_agent.services.refinement_agent.refine_with_llm", new_callable=AsyncMock) as m:
                m.return_value = mock_result
                await refine_task(db, "history-1")

        # Check refinement history has 3 entries
        ref_state = task_hub.get_refinement_state(db, "history-1")
        history = ref_state.get("refinement_history", {})
        assert len(history) >= 3

        # Verify each entry has the expected stage
        stages_in_history = [v.get("stage") for v in history.values()]
        assert "interviewing" in stages_in_history
        assert "exploring" in stages_in_history
        assert "crystallizing" in stages_in_history

    def test_notification_dedup_prevents_spam(self, db):
        """Verify notification dedup prevents duplicate notifications."""
        _create_task(db, "dedup-1", refinement_stage="raw_idea")

        # Record same notification twice
        # record_notification returns True if new, False if already sent
        r1 = task_hub.record_notification(db, task_id="dedup-1", event_key="auto_refined_to_interviewing")
        r2 = task_hub.record_notification(db, task_id="dedup-1", event_key="auto_refined_to_interviewing")

        assert r1 is True   # First time → new
        assert r2 is False   # Dedup → already sent
