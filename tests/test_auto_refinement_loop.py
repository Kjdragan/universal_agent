"""Tests for the auto-refinement loop service.

Tests cover:
  - Candidate discovery (stage filtering, cooldown, status filtering)
  - Single-task refinement (advance, question, hold, edge cases)
  - Auto-decomposition (success, failure, integration with stage advancement)
  - Batch cycle runner (feature flag, budget limits, error isolation)
  - Cooldown management
"""

from __future__ import annotations

import json
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from universal_agent import task_hub
from universal_agent.services import auto_refinement_loop
from universal_agent.services.auto_refinement_loop import (
    AUTO_PROCESSABLE_STAGES,
    _is_on_cooldown,
    _record_attempt,
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
def reset_cooldowns():
    """Reset cooldown state between tests."""
    clear_cooldowns()
    yield
    clear_cooldowns()


def _create_brainstorm(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    stage: str = "raw_idea",
    title: str = "Test brainstorm",
    description: str = "A test idea",
    priority: int = 2,
    status: str = "open",
) -> dict:
    """Helper: create a brainstorm task with specified stage."""
    task_hub.upsert_item(conn, {
        "task_id": task_id,
        "title": title,
        "description": description,
        "priority": priority,
        "status": status,
        "trigger_type": "brainstorm",
        "labels": ["brainstorm"],
    })
    # Set refinement stage directly
    conn.execute(
        "UPDATE task_hub_items SET refinement_stage = ? WHERE task_id = ?",
        (stage, task_id),
    )
    conn.commit()
    return task_hub.get_item(conn, task_id) or {}


# ── Cooldown Tests ───────────────────────────────────────────────────────────

class TestCooldown:

    def test_not_on_cooldown_initially(self):
        assert not _is_on_cooldown("test-1")

    def test_on_cooldown_after_record(self):
        _record_attempt("test-1")
        assert _is_on_cooldown("test-1")

    def test_clear_cooldowns(self):
        _record_attempt("test-1")
        clear_cooldowns()
        assert not _is_on_cooldown("test-1")

    def test_different_tasks_independent(self):
        _record_attempt("test-1")
        assert _is_on_cooldown("test-1")
        assert not _is_on_cooldown("test-2")


# ── Candidate Discovery ─────────────────────────────────────────────────────

class TestFindCandidates:

    def test_finds_brainstorm_at_raw_idea(self, db):
        _create_brainstorm(db, "b1", stage="raw_idea")
        candidates = find_refinement_candidates(db)
        assert len(candidates) == 1
        assert candidates[0]["task_id"] == "b1"

    def test_finds_brainstorm_at_each_processable_stage(self, db):
        for i, stage in enumerate(AUTO_PROCESSABLE_STAGES):
            _create_brainstorm(db, f"b{i}", stage=stage)
        candidates = find_refinement_candidates(db, max_results=10)
        assert len(candidates) == len(AUTO_PROCESSABLE_STAGES)

    def test_skips_actionable_stage(self, db):
        _create_brainstorm(db, "b1", stage="actionable")
        candidates = find_refinement_candidates(db)
        assert len(candidates) == 0

    def test_skips_non_brainstorm_tasks(self, db):
        task_hub.upsert_item(db, {
            "task_id": "t1",
            "title": "Normal task",
            "trigger_type": "immediate",
            "status": "open",
        })
        candidates = find_refinement_candidates(db)
        assert len(candidates) == 0

    def test_skips_completed_brainstorms(self, db):
        _create_brainstorm(db, "b1", stage="exploring", status="completed")
        candidates = find_refinement_candidates(db)
        assert len(candidates) == 0

    def test_skips_in_progress_brainstorms(self, db):
        _create_brainstorm(db, "b1", stage="exploring", status="in_progress")
        candidates = find_refinement_candidates(db)
        assert len(candidates) == 0

    def test_skips_cooldown_tasks(self, db):
        _create_brainstorm(db, "b1", stage="raw_idea")
        _record_attempt("b1")
        candidates = find_refinement_candidates(db)
        assert len(candidates) == 0

    def test_respects_max_results(self, db):
        for i in range(5):
            _create_brainstorm(db, f"b{i}", stage="raw_idea")
        candidates = find_refinement_candidates(db, max_results=2)
        assert len(candidates) == 2

    def test_orders_by_priority_then_age(self, db):
        _create_brainstorm(db, "b-low", stage="raw_idea", priority=3)
        _create_brainstorm(db, "b-high", stage="raw_idea", priority=1)
        candidates = find_refinement_candidates(db)
        assert candidates[0]["task_id"] == "b-high"


# ── Refinement Execution ────────────────────────────────────────────────────

class TestRefineTask:

    @pytest.mark.asyncio
    async def test_advance_stage(self, db):
        _create_brainstorm(db, "b1", stage="raw_idea")

        mock_result = {
            "recommendation": "advance",
            "next_stage": "interviewing",
            "reasoning": "Idea is clear enough",
            "questions": [],
            "enriched_description": "Improved description",
            "suggested_subtasks": [],
        }
        with patch("universal_agent.services.refinement_agent.refine_with_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_result
            result = await refine_task(db, "b1")

        assert result["action"] == "advanced"
        assert result["from_stage"] == "raw_idea"
        assert result["to_stage"] == "interviewing"

        # Verify DB was updated
        item = task_hub.get_item(db, "b1")
        assert item["refinement_stage"] == "interviewing"

    @pytest.mark.asyncio
    async def test_question_recommendation(self, db):
        _create_brainstorm(db, "b1", stage="interviewing")

        mock_result = {
            "recommendation": "question",
            "next_stage": "exploring",
            "reasoning": "Need more info",
            "questions": ["What's the budget?", "When is the deadline?"],
            "enriched_description": "",
            "suggested_subtasks": [],
        }
        with patch("universal_agent.services.refinement_agent.refine_with_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_result
            result = await refine_task(db, "b1")

        assert result["action"] == "questions"
        assert result["count"] == 2

        # Verify questions were enqueued (check via direct SQL since list_pending_questions filters by expiry)
        rows = db.execute(
            "SELECT * FROM task_hub_question_queue WHERE task_id = ?", ("b1",)
        ).fetchall()
        assert len(rows) >= 2

    @pytest.mark.asyncio
    async def test_hold_recommendation(self, db):
        _create_brainstorm(db, "b1", stage="exploring")

        mock_result = {
            "recommendation": "hold",
            "next_stage": "crystallizing",
            "reasoning": "Insufficient context",
            "questions": [],
            "enriched_description": "",
            "suggested_subtasks": [],
        }
        with patch("universal_agent.services.refinement_agent.refine_with_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_result
            result = await refine_task(db, "b1")

        assert result["action"] == "hold"
        assert result["current_stage"] == "exploring"

        # Stage should NOT change
        item = task_hub.get_item(db, "b1")
        assert item["refinement_stage"] == "exploring"

    @pytest.mark.asyncio
    async def test_not_found_skip(self, db):
        result = await refine_task(db, "nonexistent")
        assert result["action"] == "skip"
        assert result["reason"] == "not_found"

    @pytest.mark.asyncio
    async def test_non_processable_stage_skip(self, db):
        _create_brainstorm(db, "b1", stage="actionable")
        result = await refine_task(db, "b1")
        assert result["action"] == "skip"

    @pytest.mark.asyncio
    async def test_dry_run(self, db):
        _create_brainstorm(db, "b1", stage="raw_idea")

        mock_result = {
            "recommendation": "advance",
            "next_stage": "interviewing",
            "reasoning": "Ready",
            "questions": [],
            "enriched_description": "Better description",
            "suggested_subtasks": [],
        }
        with patch("universal_agent.services.refinement_agent.refine_with_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_result
            result = await refine_task(db, "b1", dry_run=True)

        assert result["action"] == "dry_run"

        # Stage should NOT change in dry run
        item = task_hub.get_item(db, "b1")
        assert item["refinement_stage"] == "raw_idea"

    @pytest.mark.asyncio
    async def test_records_cooldown(self, db):
        _create_brainstorm(db, "b1", stage="raw_idea")

        mock_result = {
            "recommendation": "hold",
            "next_stage": "interviewing",
            "reasoning": "Hold",
            "questions": [],
            "enriched_description": "",
            "suggested_subtasks": [],
        }
        with patch("universal_agent.services.refinement_agent.refine_with_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_result
            await refine_task(db, "b1")

        assert _is_on_cooldown("b1")


# ── Auto-Decomposition ──────────────────────────────────────────────────────

class TestAutoDecompose:

    @pytest.mark.asyncio
    async def test_decomposing_stage_triggers_decomposition(self, db):
        _create_brainstorm(db, "b1", stage="decomposing",
                          title="Build widget", description="A new UI widget")

        mock_subtasks = [
            {"title": "Design wireframe", "description": "Create mockup", "priority": 2},
            {"title": "Implement UI", "description": "Code the widget", "priority": 2},
        ]
        with patch("universal_agent.services.decomposition_agent.decompose_with_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_subtasks
            result = await refine_task(db, "b1")

        assert result["action"] == "decomposed"
        assert result["subtask_count"] == 2

        # Verify task advanced to actionable
        item = task_hub.get_item(db, "b1")
        assert item["refinement_stage"] == "actionable"

    @pytest.mark.asyncio
    async def test_decomposition_failure_handles_gracefully(self, db):
        _create_brainstorm(db, "b1", stage="decomposing")

        with patch("universal_agent.services.decomposition_agent.decompose_with_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = Exception("LLM timeout")
            result = await refine_task(db, "b1")

        assert result["action"] == "decomposition_failed"
        assert "timeout" in result["error"].lower()

        # Stage should NOT advance on failure
        item = task_hub.get_item(db, "b1")
        assert item["refinement_stage"] == "decomposing"

    @pytest.mark.asyncio
    async def test_advance_into_decomposing_triggers_immediate_decompose(self, db):
        _create_brainstorm(db, "b1", stage="crystallizing")

        mock_refine_result = {
            "recommendation": "advance",
            "next_stage": "decomposing",
            "reasoning": "Spec is clear",
            "questions": [],
            "enriched_description": "Crystal-clear description",
            "suggested_subtasks": [],
        }
        mock_subtasks = [
            {"title": "Sub 1", "description": "First step", "priority": 1},
        ]

        with patch("universal_agent.services.refinement_agent.refine_with_llm", new_callable=AsyncMock) as mock_refine, \
             patch("universal_agent.services.decomposition_agent.decompose_with_llm", new_callable=AsyncMock) as mock_decompose:
            mock_refine.return_value = mock_refine_result
            mock_decompose.return_value = mock_subtasks
            result = await refine_task(db, "b1")

        # Should have gone through decomposition, not just advancement
        assert result["action"] == "decomposed"
        assert result["subtask_count"] == 1

        # Should be at actionable now
        item = task_hub.get_item(db, "b1")
        assert item["refinement_stage"] == "actionable"


# ── Batch Cycle Runner ───────────────────────────────────────────────────────

class TestRunAutoRefinementCycle:

    @pytest.mark.asyncio
    async def test_disabled_returns_early(self, db):
        with patch.object(auto_refinement_loop, "AUTO_REFINEMENT_ENABLED", False):
            result = await run_auto_refinement_cycle(db)

        assert result["enabled"] is False
        assert result["processed"] == 0

    @pytest.mark.asyncio
    async def test_no_candidates_returns_zero(self, db):
        with patch.object(auto_refinement_loop, "AUTO_REFINEMENT_ENABLED", True):
            result = await run_auto_refinement_cycle(db)

        assert result["enabled"] is True
        assert result["processed"] == 0
        assert result["candidates"] == 0

    @pytest.mark.asyncio
    async def test_processes_candidates(self, db):
        _create_brainstorm(db, "b1", stage="raw_idea")
        _create_brainstorm(db, "b2", stage="exploring")

        mock_result = {
            "recommendation": "advance",
            "next_stage": "interviewing",
            "reasoning": "Ready",
            "questions": [],
            "enriched_description": "Better",
            "suggested_subtasks": [],
        }

        with patch.object(auto_refinement_loop, "AUTO_REFINEMENT_ENABLED", True), \
             patch("universal_agent.services.refinement_agent.refine_with_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_result
            result = await run_auto_refinement_cycle(db)

        assert result["enabled"] is True
        assert result["processed"] == 2

    @pytest.mark.asyncio
    async def test_respects_budget(self, db):
        for i in range(5):
            _create_brainstorm(db, f"b{i}", stage="raw_idea")

        mock_result = {
            "recommendation": "hold",
            "next_stage": "interviewing",
            "reasoning": "Hold",
            "questions": [],
            "enriched_description": "",
            "suggested_subtasks": [],
        }

        with patch.object(auto_refinement_loop, "AUTO_REFINEMENT_ENABLED", True), \
             patch.object(auto_refinement_loop, "MAX_REFINEMENTS_PER_CYCLE", 2), \
             patch("universal_agent.services.refinement_agent.refine_with_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_result
            result = await run_auto_refinement_cycle(db)

        # Should only process 2 despite 5 candidates
        assert result["processed"] == 2

    @pytest.mark.asyncio
    async def test_error_isolation(self, db):
        _create_brainstorm(db, "b-ok", stage="raw_idea")
        _create_brainstorm(db, "b-fail", stage="exploring")

        call_count = 0

        async def mock_refine(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call succeeds
                return {
                    "recommendation": "hold",
                    "next_stage": "interviewing",
                    "reasoning": "Fine",
                    "questions": [],
                    "enriched_description": "",
                    "suggested_subtasks": [],
                }
            else:
                # Second call raises
                raise RuntimeError("API explosion")

        with patch.object(auto_refinement_loop, "AUTO_REFINEMENT_ENABLED", True), \
             patch("universal_agent.services.refinement_agent.refine_with_llm", side_effect=mock_refine):
            result = await run_auto_refinement_cycle(db)

        # Both should be processed (one success, one error)
        assert result["processed"] == 2
        actions = [r["action"] for r in result["results"]]
        assert "hold" in actions
        assert "error" in actions
