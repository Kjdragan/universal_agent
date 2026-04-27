"""Tests for dispatch_service._enrich_with_routing."""

from __future__ import annotations

import builtins
from unittest.mock import patch

import pytest

from universal_agent.services.dispatch_service import _enrich_with_routing

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(
    task_id: str = "t-001",
    title: str = "Do something",
    labels: list[str] | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "title": title,
        "labels": labels or [],
    }


# ---------------------------------------------------------------------------
# TestEnrichWithRoutingBasic
# ---------------------------------------------------------------------------

class TestEnrichWithRoutingBasic:
    """Core behaviour of _enrich_with_routing."""

    def test_empty_list_returns_unchanged(self):
        result = _enrich_with_routing([])
        assert result == []

    def test_returns_same_list_reference(self):
        tasks = [_make_task()]
        result = _enrich_with_routing(tasks)
        assert result is tasks

    def test_routing_disabled_adds_simone_routing(self, monkeypatch):
        monkeypatch.setenv("UA_AGENT_ROUTING_ENABLED", "false")
        tasks = [_make_task()]
        _enrich_with_routing(tasks)
        routing = tasks[0]["_routing"]
        assert routing["agent_id"] == "simone"
        assert "confidence" in routing
        assert "reason" in routing

    def test_routing_enabled_adds_routing_keys(self, monkeypatch):
        monkeypatch.setenv("UA_AGENT_ROUTING_ENABLED", "true")
        tasks = [_make_task(title="Refactor the auth module")]
        _enrich_with_routing(tasks)
        routing = tasks[0]["_routing"]
        assert "agent_id" in routing
        assert "confidence" in routing
        assert "reason" in routing

    def test_simone_label_always_routes_to_simone(self, monkeypatch):
        """Even with routing enabled, a simone label forces simone."""
        monkeypatch.setenv("UA_AGENT_ROUTING_ENABLED", "true")
        tasks = [_make_task(title="Anything", labels=["simone"])]
        _enrich_with_routing(tasks)
        assert tasks[0]["_routing"]["agent_id"] == "simone"

    def test_multiple_tasks_all_get_routing(self, monkeypatch):
        monkeypatch.setenv("UA_AGENT_ROUTING_ENABLED", "true")
        tasks = [
            _make_task(task_id="t-001", title="Fix bug", labels=["code"]),
            _make_task(task_id="t-002", title="Send email", labels=["email"]),
            _make_task(task_id="t-003", title="Research topic", labels=["research"]),
        ]
        _enrich_with_routing(tasks)
        for t in tasks:
            assert "_routing" in t
            assert "agent_id" in t["_routing"]
            assert "confidence" in t["_routing"]
            assert "reason" in t["_routing"]

    def test_import_failure_returns_unchanged(self):
        """If agent_router cannot be imported, tasks are returned as-is."""
        tasks = [_make_task()]
        original_import = builtins.__import__

        def _failing_import(name, *args, **kwargs):
            if name == "universal_agent.services.agent_router":
                raise ImportError("simulated import failure")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_failing_import):
            result = _enrich_with_routing(tasks)
            assert result is tasks
            # No _routing key should have been added
            assert "_routing" not in tasks[0]


# ---------------------------------------------------------------------------
# TestEnrichWithRoutingGracefulDegradation
# ---------------------------------------------------------------------------

class TestEnrichWithRoutingGracefulDegradation:
    """Minimal / edge-case task dicts should not crash the enricher."""

    def test_minimal_task_dict_no_title_or_labels(self, monkeypatch):
        monkeypatch.setenv("UA_AGENT_ROUTING_ENABLED", "true")
        tasks = [{"task_id": "bare-001"}]
        _enrich_with_routing(tasks)
        assert "_routing" in tasks[0]
        routing = tasks[0]["_routing"]
        # With no title/labels/keywords, it should default to simone
        assert routing["agent_id"] == "simone"

    def test_minimal_task_dict_routing_disabled(self, monkeypatch):
        monkeypatch.setenv("UA_AGENT_ROUTING_ENABLED", "false")
        tasks = [{}]
        _enrich_with_routing(tasks)
        assert "_routing" in tasks[0]
        assert tasks[0]["_routing"]["agent_id"] == "simone"

    def test_task_with_only_labels_string(self, monkeypatch):
        """Labels as a JSON string should not crash the enricher."""
        monkeypatch.setenv("UA_AGENT_ROUTING_ENABLED", "true")
        tasks = [{"task_id": "json-labels", "labels": '["code"]'}]
        _enrich_with_routing(tasks)
        assert "_routing" in tasks[0]
