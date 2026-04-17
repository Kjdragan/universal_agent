"""Tests for the agent_router module — Simone-first multi-agent task qualification."""

from __future__ import annotations

import pytest

from universal_agent.services.agent_router import (
    AGENT_SIMONE,
    AGENT_CODER,
    AGENT_GENERAL,
    route_all_to_simone,
    route_claimed_tasks,
    route_claimed_tasks_llm,
)


def _task(title: str = "", description: str = "") -> dict:
    return {
        "task_id": "test-001",
        "title": title,
        "description": description,
    }


def test_route_all_to_simone():
    """All tasks should route to Simone regardless of content."""
    tasks = [
        _task(title="Fix auth bug"),
        _task(title="Market analysis"),
        _task(title="Check email"),
    ]
    
    buckets = route_all_to_simone(tasks)
    
    assert AGENT_SIMONE in buckets
    assert len(buckets) == 1
    assert len(buckets[AGENT_SIMONE]) == 3
    
    for task in tasks:
        assert "_routing" in task
        assert task["_routing"]["agent_id"] == AGENT_SIMONE
        assert task["_routing"]["should_delegate"] is False
        assert task["_routing"]["confidence"] == "orchestrator"


def test_route_claimed_tasks_alias():
    """The legacy alias should behave identically to route_all_to_simone."""
    tasks = [_task(title="Fix auth bug")]
    buckets = route_claimed_tasks(tasks, available_agents={AGENT_CODER, AGENT_GENERAL})
    
    assert AGENT_SIMONE in buckets
    assert len(buckets) == 1
    assert len(buckets[AGENT_SIMONE]) == 1
    assert tasks[0]["_routing"]["agent_id"] == AGENT_SIMONE


@pytest.mark.asyncio
async def test_route_claimed_tasks_llm_alias():
    """The legacy async LLM alias should also route everything to Simone."""
    tasks = [_task(title="Deep research")]
    buckets = await route_claimed_tasks_llm(tasks)
    
    assert AGENT_SIMONE in buckets
    assert len(buckets) == 1
    assert len(buckets[AGENT_SIMONE]) == 1
    assert tasks[0]["_routing"]["agent_id"] == AGENT_SIMONE
