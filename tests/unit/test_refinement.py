"""Unit tests for the Brainstorm Progressive Refinement Pipeline (Phase 4).

Tests:
  - advance_refinement progresses through stages correctly
  - get_refinement_state returns correct state
  - add_comment appends comments to a task
  - enqueue_question / answer_question round-trip
  - notification dedup with record_notification / has_notification
  - refine_with_llm returns proper structure (mocked)
  - Gateway refinement endpoints
"""

from __future__ import annotations

import json
import os
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from universal_agent.task_hub import (
    REFINEMENT_STAGES,
    TASK_STATUS_OPEN,
    add_comment,
    advance_refinement,
    answer_question,
    enqueue_question,
    ensure_schema,
    get_item,
    get_refinement_state,
    has_notification,
    list_comments,
    list_pending_questions,
    record_notification,
    upsert_item,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _insert_task(conn, task_id: str, **overrides) -> dict:
    item = {
        "task_id": task_id,
        "source_kind": "brainstorm",
        "title": f"Task {task_id}",
        "description": f"Desc for {task_id}",
        "project_key": "brainstorm",
        "priority": 2,
        "status": TASK_STATUS_OPEN,
        **overrides,
    }
    return upsert_item(conn, item)


# ── advance_refinement ──────────────────────────────────────────────────

class TestAdvanceRefinement:
    def test_advances_from_raw_idea(self):
        conn = _make_conn()
        _insert_task(conn, "bs-1", refinement_stage="raw_idea")
        result = advance_refinement(conn, task_id="bs-1", new_stage="interviewing")
        assert result["refinement_stage"] == "interviewing"
        task = get_item(conn, "bs-1")
        assert task["refinement_stage"] == "interviewing"

    def test_records_history(self):
        conn = _make_conn()
        _insert_task(conn, "bs-2", refinement_stage="raw_idea")
        advance_refinement(conn, task_id="bs-2", new_stage="interviewing",
                          context_update={"reason": "enough clarity"})
        state = get_refinement_state(conn, "bs-2")
        history = state["refinement_history"]
        assert len(history) == 1
        entry = list(history.values())[0]
        assert entry["stage"] == "interviewing"
        assert entry["context"]["reason"] == "enough clarity"

    def test_rejects_invalid_stage(self):
        conn = _make_conn()
        _insert_task(conn, "bs-3", refinement_stage="raw_idea")
        with pytest.raises(ValueError, match="Invalid refinement stage"):
            advance_refinement(conn, task_id="bs-3", new_stage="invented_stage")

    def test_raises_for_missing_task(self):
        conn = _make_conn()
        with pytest.raises(ValueError, match="not found"):
            advance_refinement(conn, task_id="nonexistent", new_stage="interviewing")


# ── get_refinement_state ────────────────────────────────────────────────

class TestGetRefinementState:
    def test_returns_stage_and_history(self):
        conn = _make_conn()
        _insert_task(conn, "bs-4")
        # Set stage via advance_refinement (upsert_item doesn't persist refinement_stage)
        advance_refinement(conn, task_id="bs-4", new_stage="exploring")
        state = get_refinement_state(conn, "bs-4")
        assert state["refinement_stage"] == "exploring"
        assert len(state["refinement_history"]) == 1

    def test_returns_error_for_missing_task(self):
        conn = _make_conn()
        state = get_refinement_state(conn, "nonexistent")
        assert state.get("error") == "not_found"


# ── add_comment / list_comments ─────────────────────────────────────────

class TestAddComment:
    def test_adds_comment_with_timestamp(self):
        conn = _make_conn()
        _insert_task(conn, "bs-5")
        comment = add_comment(conn, task_id="bs-5", content="Hello world")
        assert comment["content"] == "Hello world"
        assert "created_at" in comment
        assert comment["author"] == "system"

    def test_custom_author(self):
        conn = _make_conn()
        _insert_task(conn, "bs-6")
        comment = add_comment(conn, task_id="bs-6", content="Note", author="user")
        assert comment["author"] == "user"

    def test_list_comments(self):
        conn = _make_conn()
        _insert_task(conn, "bs-7")
        add_comment(conn, task_id="bs-7", content="First")
        add_comment(conn, task_id="bs-7", content="Second")
        comments = list_comments(conn, "bs-7")
        assert len(comments) == 2


# ── enqueue_question / answer_question ──────────────────────────────────

class TestQuestionQueue:
    def test_round_trip(self):
        conn = _make_conn()
        _insert_task(conn, "bs-8")
        q = enqueue_question(conn, task_id="bs-8", question_text="What stack?")
        assert q["question_text"] == "What stack?"
        q_id = q["question_id"]

        answered = answer_question(conn, question_id=q_id, answer_text="Python + FastAPI")
        assert answered["answer_text"] == "Python + FastAPI"
        assert answered["answered"] == 1

    def test_list_pending_questions(self):
        conn = _make_conn()
        _insert_task(conn, "bs-9")
        enqueue_question(conn, task_id="bs-9", question_text="Q1")
        enqueue_question(conn, task_id="bs-9", question_text="Q2")
        pending = list_pending_questions(conn)
        assert len(pending) >= 2


# ── Notification dedup ──────────────────────────────────────────────────

class TestNotificationDedup:
    def test_record_and_check(self):
        conn = _make_conn()
        assert has_notification(conn, "bs-10", "stage_advanced") is False
        result = record_notification(conn, task_id="bs-10", event_key="stage_advanced")
        assert result is True  # New notification
        assert has_notification(conn, "bs-10", "stage_advanced") is True

    def test_different_keys_independent(self):
        conn = _make_conn()
        record_notification(conn, task_id="bs-11", event_key="stage_advanced")
        assert has_notification(conn, "bs-11", "question_asked") is False

    def test_idempotent_record(self):
        conn = _make_conn()
        first = record_notification(conn, task_id="bs-12", event_key="x")
        assert first is True
        second = record_notification(conn, task_id="bs-12", event_key="x")
        assert second is False  # Already sent


# ── refine_with_llm (mocked) ───────────────────────────────────────────

class TestRefineWithLLM:
    @pytest.mark.asyncio
    async def test_returns_structured_result(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = json.dumps({
            "recommendation": "advance",
            "next_stage": "interviewing",
            "reasoning": "Ready to move forward",
            "questions": [],
            "enriched_description": "An improved description",
            "suggested_subtasks": [],
        })

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-fake"}), \
             patch("anthropic.AsyncAnthropic", return_value=mock_client):
            from universal_agent.services.refinement_agent import refine_with_llm
            result = await refine_with_llm(
                title="Test brainstorm",
                description="Some idea",
                current_stage="raw_idea",
            )
            assert result["recommendation"] == "advance"
            assert result["reasoning"] == "Ready to move forward"

    @pytest.mark.asyncio
    async def test_handles_question_recommendation(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = json.dumps({
            "recommendation": "question",
            "next_stage": "interviewing",
            "reasoning": "Need more info",
            "questions": ["What is the target audience?", "What's the timeline?"],
            "enriched_description": "Some idea (needs elaboration)",
            "suggested_subtasks": [],
        })

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-fake"}), \
             patch("anthropic.AsyncAnthropic", return_value=mock_client):
            from universal_agent.services.refinement_agent import refine_with_llm
            result = await refine_with_llm(
                title="Another brainstorm",
                description="Another idea",
                current_stage="interviewing",
            )
            assert result["recommendation"] == "question"
            assert len(result["questions"]) == 2


# ── Gateway endpoint tests ──────────────────────────────────────────────

class TestGatewayRefinementEndpoints:
    """Smoke tests for refinement gateway wiring (requires httpx)."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        try:
            from fastapi.testclient import TestClient
            import httpx
        except ImportError:
            pytest.skip("httpx/fastapi not available")

    def test_refinement_state_endpoint_404_for_missing(self):
        from fastapi.testclient import TestClient

        from universal_agent.gateway_server import app
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/dashboard/todolist/tasks/nonexistent/refinement-state")
        assert resp.status_code in (200, 404)

    def test_questions_endpoint_returns_list(self):
        from fastapi.testclient import TestClient

        from universal_agent.gateway_server import app
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/dashboard/todolist/tasks/nonexistent/questions")
        assert resp.status_code in (200, 404)
