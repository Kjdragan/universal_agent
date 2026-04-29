"""Tests for _create_heartbeat_remediation_task and its integration into
_process_heartbeat_investigation_notification."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional
from unittest.mock import patch

import pytest


@pytest.fixture()
def _activity_db(tmp_path, monkeypatch):
    """Point the activity DB at a temporary path so upsert_item writes to a real SQLite."""
    db_path = str(tmp_path / "activity_state.db")
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", db_path)
    return db_path


# ---------------------------------------------------------------------------
# Direct unit tests for _create_heartbeat_remediation_task
# ---------------------------------------------------------------------------


def test_creates_task_hub_item_with_recommended_step(_activity_db):
    """When given a recommended_next_step, the function should create an agent-ready task."""
    from universal_agent import gateway_server as gs

    result = gs._create_heartbeat_remediation_task(
        origin_id="ntf_test_001",
        classification="parser_false_positive",
        recommended_next_step="Fix the regex in parser.py line 42",
        proposed_changes=["Correct regex pattern in parser.py"],
        email_summary="Parser incorrectly flagged healthy response as error.",
        session_id="sess_abc",
    )

    assert result is not None
    assert result["task_id"] == "heartbeat_fix:parser_false_positive"
    assert result["source_kind"] == "heartbeat_remediation"
    assert result["agent_ready"] is True or result["agent_ready"] == 1
    assert result["status"] == "open"
    assert result["project_key"] == "proactive"
    assert result["trigger_type"] == "immediate"
    assert "heartbeat-fix" in result.get("labels", [])
    assert "Fix the regex" in result.get("description", "")


def test_heartbeat_remediation_description_includes_workflow_lineage(_activity_db):
    from universal_agent import gateway_server as gs

    result = gs._create_heartbeat_remediation_task(
        origin_id="ntf_test_lineage",
        classification="heartbeat_timeout",
        recommended_next_step="Restart the worker and verify health.",
        proposed_changes=["Restart worker process"],
        email_summary="Heartbeat execution timed out twice.",
        session_id="session_heartbeat_1",
        run_id="run_heartbeat_1",
        attempt_id="attempt_heartbeat_2",
    )

    assert result is not None
    description = str(result.get("description") or "")
    assert "Workflow run: run_heartbeat_1" in description
    assert "Workflow attempt: attempt_heartbeat_2" in description
    assert "Provider session: session_heartbeat_1" in description


def test_deterministic_task_id_upserts_not_duplicates(_activity_db):
    """Calling twice with the same classification should upsert, not create a new task."""
    from universal_agent import gateway_server as gs

    result_1 = gs._create_heartbeat_remediation_task(
        origin_id="ntf_first",
        classification="code_regression",
        recommended_next_step="Revert commit abc123",
        proposed_changes=[],
        email_summary="Regression detected.",
        session_id=None,
    )
    result_2 = gs._create_heartbeat_remediation_task(
        origin_id="ntf_second",
        classification="code_regression",
        recommended_next_step="Actually revert commit def456",
        proposed_changes=[],
        email_summary="Updated regression context.",
        session_id=None,
    )

    assert result_1 is not None
    assert result_2 is not None
    # Same task_id → upsert, not duplicate
    assert result_1["task_id"] == result_2["task_id"] == "heartbeat_fix:code_regression"
    # Second call should update the description
    assert "def456" in result_2.get("description", "")


def test_returns_none_when_feature_disabled(_activity_db, monkeypatch):
    """When the feature flag is off, no task should be created."""
    monkeypatch.setenv("UA_HEARTBEAT_AUTO_REMEDIATION_ENABLED", "0")
    # Force re-evaluation of the module-level flag
    import universal_agent.gateway_server as gs

    original = gs._HEARTBEAT_AUTO_REMEDIATION_ENABLED
    gs._HEARTBEAT_AUTO_REMEDIATION_ENABLED = False
    try:
        result = gs._create_heartbeat_remediation_task(
            origin_id="ntf_disabled",
            classification="noise",
            recommended_next_step="Fix something",
            proposed_changes=[],
            email_summary="",
            session_id=None,
        )
        assert result is None
    finally:
        gs._HEARTBEAT_AUTO_REMEDIATION_ENABLED = original


def test_handles_no_proposed_changes(_activity_db):
    """Should succeed even with empty proposed_changes and no email_summary."""
    from universal_agent import gateway_server as gs

    result = gs._create_heartbeat_remediation_task(
        origin_id="ntf_minimal",
        classification="minor_issue",
        recommended_next_step="Check logs",
        proposed_changes=None,
        email_summary="",
        session_id=None,
    )
    assert result is not None
    assert result["task_id"] == "heartbeat_fix:minor_issue"
    assert "Check logs" in result.get("description", "")


# ---------------------------------------------------------------------------
# Integration: verify _process_heartbeat_investigation_notification calls
# _create_heartbeat_remediation_task when appropriate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_investigation_creates_remediation_task_for_actionable_finding(
    _activity_db, monkeypatch
):
    """Investigation with recommended_next_step and operator_review_required=False
    should invoke _create_heartbeat_remediation_task."""
    import universal_agent.gateway_server as gs

    # Stub out functions that touch external state
    monkeypatch.setattr(gs, "_get_activity_event", lambda _id: {"metadata": {}})
    monkeypatch.setattr(
        gs, "_update_notification_record", lambda *a, **kw: None
    )
    monkeypatch.setattr(gs, "_record_activity_audit", lambda **kw: None)
    nudge_reasons: list[str] = []
    monkeypatch.setattr(
        "universal_agent.services.idle_dispatch_loop.nudge_dispatch",
        lambda reason="external": nudge_reasons.append(reason),
    )

    created_tasks: list[dict] = []
    original_create = gs._create_heartbeat_remediation_task

    def _capture_create(**kwargs):
        result = original_create(**kwargs)
        if result is not None:
            created_tasks.append(result)
        return result

    monkeypatch.setattr(gs, "_create_heartbeat_remediation_task", _capture_create)

    payload = {
        "metadata": {
            "source_notification_id": "ntf_integration_001",
            "classification": "parser_false_positive",
            "operator_review_required": False,
            "recommended_next_step": "Fix the parser regex",
            "proposed_changes": ["Update regex in parser.py"],
            "unknown_rule_count": 0,
        },
        "session_id": "sess_test",
    }

    await gs._process_heartbeat_investigation_notification(payload)

    assert len(created_tasks) == 1
    assert created_tasks[0]["task_id"] == "heartbeat_fix:parser_false_positive"
    assert nudge_reasons == ["heartbeat_remediation:heartbeat_fix:parser_false_positive"]


@pytest.mark.asyncio
async def test_investigation_honors_simone_autonomous_remediation_decision(
    _activity_db, monkeypatch
):
    """A memory-backed Simone decision should create a fix task without emailing Kevin,
    even when the original finding was unknown."""
    import universal_agent.gateway_server as gs

    monkeypatch.setattr(gs, "_get_activity_event", lambda _id: {"metadata": {}})
    updated_records: list[dict] = []
    monkeypatch.setattr(
        gs,
        "_update_notification_record",
        lambda *a, **kw: updated_records.append({"args": a, "kwargs": kw}),
    )
    monkeypatch.setattr(gs, "_record_activity_audit", lambda **kw: None)
    notifications: list[dict] = []
    monkeypatch.setattr(gs, "_add_notification", lambda **kw: notifications.append(kw) or kw)

    async def _unexpected_operator_email(*args, **kwargs):
        raise AssertionError("operator email should not be sent")

    monkeypatch.setattr(gs, "_notify_operator_of_heartbeat_recommendation", _unexpected_operator_email)

    nudge_reasons: list[str] = []
    monkeypatch.setattr(
        "universal_agent.services.idle_dispatch_loop.nudge_dispatch",
        lambda reason="external": nudge_reasons.append(reason),
    )

    created_tasks: list[dict] = []
    original_create = gs._create_heartbeat_remediation_task

    def _capture_create(**kwargs):
        result = original_create(**kwargs)
        if result is not None:
            created_tasks.append(result)
        return result

    monkeypatch.setattr(gs, "_create_heartbeat_remediation_task", _capture_create)

    payload = {
        "metadata": {
            "source_notification_id": "ntf_auto_decision",
            "classification": "code_regression_plus_known_noise",
            "operator_review_required": False,
            "unknown_rule_count": 1,
            "recommended_next_step": "Fix disable_local_memory NameError in main.py",
            "proposed_changes": ["Initialize the fast-path memory-disabled guard."],
            "autonomous_remediation_approved": True,
            "autonomous_remediation_confidence": "high",
            "autonomous_remediation_rationale": "Known bounded code regression with prior memory.",
            "memory_evidence": ["memory/heartbeat_schema_fix.md showed prior heartbeat prompt repair pattern."],
        },
        "session_id": "sess_test",
    }

    await gs._process_heartbeat_investigation_notification(payload)

    assert len(created_tasks) == 1
    assert created_tasks[0]["task_id"] == "heartbeat_fix:code_regression_plus_known_noise"
    assert notifications == []
    assert nudge_reasons == [
        "heartbeat_remediation:heartbeat_fix:code_regression_plus_known_noise"
    ]
    assert any(
        item["kwargs"].get("requires_action") is False for item in updated_records
    )


def test_autonomous_remediation_decision_is_blocked_for_secret_or_policy_changes():
    import universal_agent.gateway_server as gs

    allowed = gs._heartbeat_autonomous_remediation_allowed(
        {
            "classification": "credential_rotation",
            "autonomous_remediation_approved": True,
            "autonomous_remediation_confidence": "high",
            "proposed_changes": ["Rotate API keys and update Tailscale ACL."],
        },
        recommended_next_step="Rotate API keys and update Tailscale ACL.",
    )

    assert allowed is False


@pytest.mark.asyncio
async def test_investigation_skips_remediation_when_operator_review_required(
    _activity_db, monkeypatch
):
    """When operator_review_required=True, no remediation task should be created
    (the existing email path handles that)."""
    import universal_agent.gateway_server as gs

    monkeypatch.setattr(gs, "_get_activity_event", lambda _id: {"metadata": {}})
    monkeypatch.setattr(
        gs, "_update_notification_record", lambda *a, **kw: None
    )
    monkeypatch.setattr(gs, "_record_activity_audit", lambda **kw: None)
    # Block the email/notify path which requires more stubs
    monkeypatch.setattr(
        gs,
        "_notify_operator_of_heartbeat_recommendation",
        lambda *a, **kw: (False, "stubbed"),
    )
    monkeypatch.setattr(gs, "_add_notification", lambda **kw: {"id": "stub"})
    nudge_reasons: list[str] = []
    monkeypatch.setattr(
        "universal_agent.services.idle_dispatch_loop.nudge_dispatch",
        lambda reason="external": nudge_reasons.append(reason),
    )

    created_tasks: list[dict] = []
    original_create = gs._create_heartbeat_remediation_task

    def _capture_create(**kwargs):
        result = original_create(**kwargs)
        if result is not None:
            created_tasks.append(result)
        return result

    monkeypatch.setattr(gs, "_create_heartbeat_remediation_task", _capture_create)

    payload = {
        "metadata": {
            "source_notification_id": "ntf_operator_review",
            "classification": "security_issue",
            "operator_review_required": True,
            "recommended_next_step": "Rotate API keys",
            "proposed_changes": [],
            "unknown_rule_count": 0,
        },
        "session_id": "sess_test",
    }

    await gs._process_heartbeat_investigation_notification(payload)

    # Should NOT have created a remediation task
    assert len(created_tasks) == 0
    assert nudge_reasons == []
