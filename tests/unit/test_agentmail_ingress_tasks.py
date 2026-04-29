from __future__ import annotations

import pytest

from universal_agent.services.agentmail_service import (
    _extract_inbound_email_tasks,
    _trusted_triage_is_non_action,
)


@pytest.mark.asyncio
async def test_default_email_ingress_keeps_single_canonical_task(monkeypatch):
    monkeypatch.delenv("UA_AGENTMAIL_SPLIT_DISJOINT_TASKS", raising=False)

    tasks = await _extract_inbound_email_tasks(
        subject="Weekly brief",
        body="Research topic A. Also include the latest numbers.",
    )

    assert len(tasks) == 1
    assert tasks[0]["task_content"] == "Research topic A. Also include the latest numbers."
    assert tasks[0]["reasoning"] == "Canonical single inbound request"


@pytest.mark.asyncio
async def test_email_ingress_can_opt_into_disjoint_task_splitting(monkeypatch):
    monkeypatch.setenv("UA_AGENTMAIL_SPLIT_DISJOINT_TASKS", "1")

    async def _fake_extract_disjointed_tasks(*, subject: str, body: str):
        assert subject == "Split this"
        assert body == "Task one. Task two."
        return [
            {"task_content": "Task one", "reasoning": "first"},
            {"task_content": "Task two", "reasoning": "second"},
        ]

    monkeypatch.setattr(
        "universal_agent.services.llm_classifier.extract_disjointed_tasks",
        _fake_extract_disjointed_tasks,
    )

    tasks = await _extract_inbound_email_tasks(
        subject="Split this",
        body="Task one. Task two.",
    )

    assert [task["task_content"] for task in tasks] == ["Task one", "Task two"]


def test_trusted_triage_non_action_closes_acknowledgements():
    triage = {
        "safety_status": "clean",
        "routing_decision": "trusted_execute",
        "classification": "status_update",
        "raw_text": "action_items:\n- no action required",
    }

    assert _trusted_triage_is_non_action(triage) is True


def test_trusted_triage_non_action_keeps_actionable_instructions():
    triage = {
        "safety_status": "clean",
        "routing_decision": "trusted_execute",
        "classification": "instruction",
        "raw_text": "action_items:\n1. check the deployment",
    }

    assert _trusted_triage_is_non_action(triage) is False
