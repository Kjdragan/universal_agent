"""Tests for the LLM classifier service.

Tests cover:
  - JSON response parsing (with/without markdown fencing)
  - Priority classification (LLM path mocked, fallback path)
  - Agent routing (LLM path mocked, fallback path, availability filtering)
  - Calendar task description generation (LLM path mocked, fallback path)
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from universal_agent.services.llm_classifier import (
    _parse_json_response,
    classify_priority,
    classify_agent_route,
    generate_calendar_task_description,
    ClassificationError,
)


# ── JSON Parsing ─────────────────────────────────────────────────────────────

class TestParseJsonResponse:

    def test_plain_json(self):
        raw = '{"priority": "p2", "reasoning": "normal task"}'
        result = _parse_json_response(raw)
        assert result["priority"] == "p2"
        assert result["reasoning"] == "normal task"

    def test_json_with_markdown_fencing(self):
        raw = '```json\n{"priority": "p1", "reasoning": "urgent"}\n```'
        result = _parse_json_response(raw)
        assert result["priority"] == "p1"

    def test_json_with_plain_fencing(self):
        raw = '```\n{"agent_id": "simone"}\n```'
        result = _parse_json_response(raw)
        assert result["agent_id"] == "simone"

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json_response("not json at all")

    def test_empty_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json_response("")


# ── Priority Classification ──────────────────────────────────────────────────

class TestClassifyPriority:

    @pytest.mark.asyncio
    async def test_llm_success(self):
        mock_response = '{"priority": "p1", "reasoning": "urgent meeting"}'
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await classify_priority(
                title="Board Review",
                description="Quarterly board presentation",
                source="calendar",
                sender_trusted=True,
            )
            assert result["priority"] == 1
            assert result["method"] == "llm"
            assert "urgent" in result["reasoning"]

    @pytest.mark.asyncio
    async def test_llm_returns_p0(self):
        mock_response = '{"priority": "p0", "reasoning": "system emergency"}'
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await classify_priority(title="SYSTEM DOWN")
            assert result["priority"] == 0
            assert result["method"] == "llm"

    @pytest.mark.asyncio
    async def test_llm_returns_p3(self):
        mock_response = '{"priority": "p3", "reasoning": "informational only"}'
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await classify_priority(title="FYI: Newsletter")
            assert result["priority"] == 3

    @pytest.mark.asyncio
    async def test_fallback_on_llm_error(self):
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = Exception("API down")
            result = await classify_priority(
                title="Test Task",
                fallback_priority=2,
            )
            assert result["priority"] == 2
            assert result["method"] == "fallback"

    @pytest.mark.asyncio
    async def test_fallback_on_bad_json(self):
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "I think this is P2"
            result = await classify_priority(
                title="Test Task",
                fallback_priority=2,
            )
            assert result["priority"] == 2
            assert result["method"] == "fallback"

    @pytest.mark.asyncio
    async def test_unknown_priority_defaults_to_p2(self):
        mock_response = '{"priority": "p5", "reasoning": "some reason"}'
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await classify_priority(title="Test")
            assert result["priority"] == 2  # default

    @pytest.mark.asyncio
    async def test_custom_fallback_priority(self):
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = Exception("timeout")
            result = await classify_priority(
                title="Low Priority",
                fallback_priority=3,
            )
            assert result["priority"] == 3


# ── Agent Routing ────────────────────────────────────────────────────────────

class TestClassifyAgentRoute:

    @pytest.mark.asyncio
    async def test_routes_to_coder(self):
        mock_response = '{"agent_id": "vp.coder.primary", "confidence": "high", "reasoning": "code task"}'
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await classify_agent_route(
                title="Fix the authentication bug",
                description="The login endpoint returns 500",
            )
            assert result["agent_id"] == "vp.coder.primary"
            assert result["confidence"] == "high"
            assert result["should_delegate"] is True
            assert result["method"] == "llm"

    @pytest.mark.asyncio
    async def test_routes_to_atlas(self):
        mock_response = '{"agent_id": "vp.general.primary", "confidence": "high", "reasoning": "research"}'
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await classify_agent_route(
                title="Research competitive landscape",
            )
            assert result["agent_id"] == "vp.general.primary"
            assert result["should_delegate"] is True

    @pytest.mark.asyncio
    async def test_routes_to_simone(self):
        mock_response = '{"agent_id": "simone", "confidence": "high", "reasoning": "coordination"}'
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await classify_agent_route(
                title="Schedule team sync",
            )
            assert result["agent_id"] == "simone"
            assert result["should_delegate"] is False

    @pytest.mark.asyncio
    async def test_invalid_agent_falls_back_to_simone(self):
        mock_response = '{"agent_id": "unknown_agent", "confidence": "high", "reasoning": "?"}'
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await classify_agent_route(title="Test")
            assert result["agent_id"] == "simone"

    @pytest.mark.asyncio
    async def test_unavailable_agent_falls_back(self):
        mock_response = '{"agent_id": "vp.coder.primary", "confidence": "high", "reasoning": "code task"}'
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await classify_agent_route(
                title="Fix bug",
                available_agents=frozenset({"simone"}),  # coder not available
            )
            assert result["agent_id"] == "simone"
            assert result["confidence"] == "fallback"
            assert "unavailable" in result["reasoning"]

    @pytest.mark.asyncio
    async def test_fallback_on_llm_error(self):
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = Exception("API error")
            result = await classify_agent_route(title="Something")
            assert result["agent_id"] == "simone"
            assert result["method"] == "fallback"
            assert result["should_delegate"] is False


# ── Calendar Description Generation ──────────────────────────────────────────

class TestGenerateCalendarTaskDescription:

    @pytest.mark.asyncio
    async def test_llm_success(self):
        mock_response = json.dumps({
            "task_description": "Prepare agenda items and review last quarter metrics before the board meeting.",
            "suggested_labels": ["meeting-prep", "leadership"],
        })
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await generate_calendar_task_description(
                title="Board Meeting",
                description="Quarterly review of company metrics",
                location="Conference Room A",
                attendees=["ceo@company.com", "cfo@company.com"],
                duration_minutes=60,
                organizer="ceo@company.com",
                fallback_description="Board Meeting in Conference Room A",
            )
            assert "agenda" in result["task_description"].lower()
            assert result["method"] == "llm"
            assert "meeting-prep" in result["suggested_labels"]

    @pytest.mark.asyncio
    async def test_fallback_on_error(self):
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = Exception("timeout")
            result = await generate_calendar_task_description(
                title="Standup",
                fallback_description="Daily standup meeting",
            )
            assert result["task_description"] == "Daily standup meeting"
            assert result["method"] == "fallback"
            assert result["suggested_labels"] == []

    @pytest.mark.asyncio
    async def test_fallback_on_bad_json(self):
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "Here's what to do: prepare the slides"
            result = await generate_calendar_task_description(
                title="Presentation",
                fallback_description="Prepare presentation",
            )
            assert result["task_description"] == "Prepare presentation"
            assert result["method"] == "fallback"

    @pytest.mark.asyncio
    async def test_empty_title(self):
        mock_response = json.dumps({
            "task_description": "Prepare for upcoming event.",
            "suggested_labels": [],
        })
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await generate_calendar_task_description(
                title="",
                fallback_description="Unknown event",
            )
            assert result["method"] == "llm"


# ── ClassificationError ──────────────────────────────────────────────────────

class TestClassificationError:

    def test_is_exception(self):
        assert issubclass(ClassificationError, Exception)

    def test_message(self):
        err = ClassificationError("no API key")
        assert str(err) == "no API key"
