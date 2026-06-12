"""Tests for the limiter-routed _call_llm_recap_evaluator seam.

Verifies that _call_llm_recap_evaluator:
- routes through llm_classifier._call_llm (not a raw Anthropic client)
- passes the model resolved from UA_PROACTIVE_RECAP_LLM_MODEL
- parses the JSON response correctly
- propagates exceptions so _evaluate_recap falls back to the heuristic
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from universal_agent.services import proactive_work_recap
from universal_agent.services.proactive_work_recap import (
    _call_llm_recap_evaluator,
    _evaluate_recap,
    upsert_recap_for_task,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_task(task_id: str = "recap-task-001") -> dict:
    return {
        "task_id": task_id,
        "source_kind": "proactive_codie",
        "title": "Test proactive work item",
        "description": "Write a brief for the operator.",
        "status": "completed",
        "metadata": {},
    }


def _make_assignment() -> dict:
    return {
        "assignment_id": "asg-001",
        "agent_id": "vp.coder.primary",
        "provider_session_id": "sess-001",
        "workspace_dir": "/tmp/fake_workspace",
        "state": "completed",
        "result_summary": "Brief written and committed.",
    }


def _make_evidence(workspace_exists: bool = False) -> dict:
    return {
        "action": "complete",
        "reason": "Work done.",
        "workspace_dir": "/tmp/fake_workspace",
        "work_products": ["work_products/brief.md"],
        "transcript_tail": "Agent committed the brief.",
        "run_log_tail": "",
        "workspace_exists": workspace_exists,
        "packet_dir": "",
        "packet_exists": False,
    }


_VALID_LLM_RESPONSE = json.dumps({
    "idea": "Write a brief for the operator",
    "implemented": "Authored brief.md and committed to repo",
    "known_issues": "",
    "success_assessment": "Successful — artifact present and committed",
    "recommended_next_action": "Review brief in three-panel UI",
    "confidence": 0.88,
})


# ── _call_llm_recap_evaluator routes through llm_classifier._call_llm ───────


def test_recap_evaluator_calls_llm_classifier_not_raw_anthropic(monkeypatch):
    """_call_llm_recap_evaluator must call llm_classifier._call_llm, not Anthropic()."""
    mock_call_llm = AsyncMock(return_value=_VALID_LLM_RESPONSE)

    with patch("universal_agent.services.proactive_work_recap._call_llm_recap_evaluator") as _:
        pass  # just ensure the function is importable

    # Patch the _call_llm import inside the module under test
    with patch(
        "universal_agent.services.llm_classifier._call_llm",
        new=mock_call_llm,
    ):
        result = _call_llm_recap_evaluator(
            task=_make_task(),
            assignment=_make_assignment(),
            action="complete",
            reason="done",
            evidence=_make_evidence(),
        )

    mock_call_llm.assert_called_once()
    assert result["idea"] == "Write a brief for the operator"
    assert result["implemented"] == "Authored brief.md and committed to repo"
    assert result["confidence"] == 0.88
    assert result["evaluation_status"] == "llm_evaluated"
    assert result["raw_model_output"]["evaluator"] == "llm_recap_v1"


def test_recap_evaluator_passes_model_from_env(monkeypatch):
    """Model param must come from UA_PROACTIVE_RECAP_LLM_MODEL env var."""
    monkeypatch.setenv("UA_PROACTIVE_RECAP_LLM_MODEL", "glm-5-turbo")

    captured_model: list[str] = []

    async def fake_call_llm(*, system, user, max_tokens, model):
        captured_model.append(model)
        return _VALID_LLM_RESPONSE

    with patch("universal_agent.services.llm_classifier._call_llm", new=fake_call_llm):
        result = _call_llm_recap_evaluator(
            task=_make_task(),
            assignment=_make_assignment(),
            action="complete",
            reason="done",
            evidence=_make_evidence(),
        )

    assert len(captured_model) == 1
    assert captured_model[0] == "glm-5-turbo"
    assert result["evaluation_status"] == "llm_evaluated"


def test_recap_evaluator_passes_max_tokens_900(monkeypatch):
    """max_tokens must be 900 as specified."""
    captured: list[dict] = []

    async def fake_call_llm(*, system, user, max_tokens, model):
        captured.append({"max_tokens": max_tokens})
        return _VALID_LLM_RESPONSE

    with patch("universal_agent.services.llm_classifier._call_llm", new=fake_call_llm):
        _call_llm_recap_evaluator(
            task=_make_task(),
            assignment=_make_assignment(),
            action="complete",
            reason="done",
            evidence=_make_evidence(),
        )

    assert captured[0]["max_tokens"] == 900


def test_recap_evaluator_raises_on_llm_failure():
    """When _call_llm raises, _call_llm_recap_evaluator must propagate the exception."""

    async def failing_llm(*, system, user, max_tokens, model):
        raise RuntimeError("ZAI 429")

    with patch("universal_agent.services.llm_classifier._call_llm", new=failing_llm):
        with pytest.raises(RuntimeError, match="ZAI 429"):
            _call_llm_recap_evaluator(
                task=_make_task(),
                assignment=_make_assignment(),
                action="complete",
                reason="done",
                evidence=_make_evidence(),
            )


def test_recap_evaluator_raises_on_invalid_json():
    """When _call_llm returns non-JSON, _call_llm_recap_evaluator must raise."""

    async def bad_json_llm(*, system, user, max_tokens, model):
        return "not json at all"

    with patch("universal_agent.services.llm_classifier._call_llm", new=bad_json_llm):
        with pytest.raises(Exception):
            _call_llm_recap_evaluator(
                task=_make_task(),
                assignment=_make_assignment(),
                action="complete",
                reason="done",
                evidence=_make_evidence(),
            )


# ── _evaluate_recap falls back to heuristic when evaluator raises ─────────


def test_evaluate_recap_falls_back_to_heuristic_when_llm_fails(monkeypatch):
    """When _call_llm_recap_evaluator raises, _evaluate_recap returns heuristic fallback."""
    monkeypatch.setenv("UA_PROACTIVE_RECAP_LLM_ENABLED", "1")

    async def failing_llm(*, system, user, max_tokens, model):
        raise RuntimeError("rate limited")

    task = _make_task()
    with patch("universal_agent.services.llm_classifier._call_llm", new=failing_llm):
        result = _evaluate_recap(
            task=task,
            assignment=_make_assignment(),
            action="complete",
            reason="Work done.",
            evidence=_make_evidence(),
        )

    assert result["evaluation_status"] == "llm_failed_fallback"
    assert result["raw_model_output"]["llm_error"] == "rate limited"
    # Heuristic fields must still be populated
    assert result["idea"] == task["title"]
    assert result["implemented"]


def test_evaluate_recap_llm_evaluated_end_to_end(monkeypatch):
    """When LLM succeeds, _evaluate_recap returns llm_evaluated status."""
    monkeypatch.setenv("UA_PROACTIVE_RECAP_LLM_ENABLED", "1")

    async def good_llm(*, system, user, max_tokens, model):
        return _VALID_LLM_RESPONSE

    with patch("universal_agent.services.llm_classifier._call_llm", new=good_llm):
        result = _evaluate_recap(
            task=_make_task(),
            assignment=_make_assignment(),
            action="complete",
            reason="done",
            evidence=_make_evidence(),
        )

    assert result["evaluation_status"] == "llm_evaluated"
    assert result["idea"] == "Write a brief for the operator"
    assert result["confidence"] == 0.88


# ── upsert_recap_for_task integration (DB + mocked LLM) ──────────────────


def _make_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def test_upsert_recap_uses_llm_evaluated_status_when_enabled(monkeypatch, tmp_path):
    """upsert_recap_for_task stores llm_evaluated when LLM succeeds."""
    monkeypatch.setenv("UA_PROACTIVE_RECAP_LLM_ENABLED", "1")

    async def good_llm(*, system, user, max_tokens, model):
        return _VALID_LLM_RESPONSE

    db_path = tmp_path / "recap.db"
    task = _make_task("recap-upsert-001")

    with _make_conn(db_path) as conn, patch(
        "universal_agent.services.llm_classifier._call_llm", new=good_llm
    ):
        stored = upsert_recap_for_task(conn, task=task, action="complete", reason="done")

    assert stored is not None
    assert stored["evaluation_status"] == "llm_evaluated"
    assert stored["idea"] == "Write a brief for the operator"


def test_upsert_recap_stores_fallback_when_llm_fails(monkeypatch, tmp_path):
    """upsert_recap_for_task stores llm_failed_fallback when LLM raises."""
    monkeypatch.setenv("UA_PROACTIVE_RECAP_LLM_ENABLED", "1")

    async def failing_llm(*, system, user, max_tokens, model):
        raise RuntimeError("connection refused")

    db_path = tmp_path / "recap_fallback.db"
    task = _make_task("recap-upsert-002")

    with _make_conn(db_path) as conn, patch(
        "universal_agent.services.llm_classifier._call_llm", new=failing_llm
    ):
        stored = upsert_recap_for_task(conn, task=task, action="complete", reason="done")

    assert stored is not None
    assert stored["evaluation_status"] == "llm_failed_fallback"
    assert "connection refused" in stored["raw_model_output"].get("llm_error", "")
