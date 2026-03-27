"""Tests for the agent_router module — multi-agent task qualification."""

from __future__ import annotations

import os
import pytest
from unittest.mock import patch

from universal_agent.services.agent_router import (
    AGENT_SIMONE,
    AGENT_CODER,
    AGENT_GENERAL,
    qualify_agent,
    route_claimed_tasks,
    _is_routing_enabled,
    _get_enabled_agents,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_AGENTS = frozenset({AGENT_SIMONE, AGENT_CODER, AGENT_GENERAL})


def _task(
    title: str = "",
    labels: list[str] | None = None,
    project_key: str = "immediate",
    source_kind: str = "internal",
    description: str = "",
) -> dict:
    return {
        "task_id": "test-001",
        "title": title,
        "description": description,
        "labels": labels or [],
        "project_key": project_key,
        "source_kind": source_kind,
    }


# ---------------------------------------------------------------------------
# Label-based routing
# ---------------------------------------------------------------------------

class TestLabelRouting:
    """Tasks with explicit labels should route to the matching agent."""

    def test_code_label_routes_to_coder(self):
        result = qualify_agent(
            _task(title="Fix auth bug", labels=["code"]),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_CODER
        assert result["confidence"] == "label"
        assert result["should_delegate"] is True

    def test_refactor_label(self):
        result = qualify_agent(
            _task(title="Refactor dispatch", labels=["refactor"]),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_CODER

    def test_deploy_label(self):
        result = qualify_agent(
            _task(title="Deploy to staging", labels=["deploy"]),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_CODER

    def test_research_label_routes_to_general(self):
        result = qualify_agent(
            _task(title="Market analysis", labels=["research"]),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_GENERAL
        assert result["confidence"] == "label"
        assert result["should_delegate"] is True

    def test_content_label(self):
        result = qualify_agent(
            _task(title="Write blog post", labels=["content"]),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_GENERAL

    def test_scout_label(self):
        result = qualify_agent(
            _task(title="Competitive scouting", labels=["scout"]),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_GENERAL

    def test_simone_label_takes_priority(self):
        """Simone labels override even if coder labels are present."""
        result = qualify_agent(
            _task(title="Coordinate deploy email", labels=["email", "code"]),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_SIMONE
        assert result["should_delegate"] is False

    def test_brainstorm_label_stays_with_simone(self):
        result = qualify_agent(
            _task(title="Brainstorm new feature", labels=["brainstorm"]),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_SIMONE

    def test_reflection_label(self):
        result = qualify_agent(
            _task(title="Overnight reflection", labels=["reflection"]),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_SIMONE

    def test_explicit_vp_coder_label(self):
        result = qualify_agent(
            _task(title="Anything", labels=["vp-coder"]),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_CODER

    def test_explicit_vp_general_label(self):
        result = qualify_agent(
            _task(title="Anything", labels=["vp-general"]),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_GENERAL


# ---------------------------------------------------------------------------
# Keyword heuristics
# ---------------------------------------------------------------------------

class TestKeywordRouting:
    """When no labels match, fall back to keyword heuristics."""

    def test_refactor_keyword(self):
        result = qualify_agent(
            _task(title="Refactor the authentication module"),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_CODER
        assert result["confidence"] == "keyword"

    def test_debug_keyword(self):
        result = qualify_agent(
            _task(title="Debug connection timeout issues"),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_CODER

    def test_implement_keyword(self):
        result = qualify_agent(
            _task(title="Implement the new webhook handler"),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_CODER

    def test_research_keyword(self):
        result = qualify_agent(
            _task(title="Research competitor pricing strategies"),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_GENERAL
        assert result["confidence"] == "keyword"

    def test_analyze_keyword(self):
        result = qualify_agent(
            _task(title="Analyze user engagement metrics"),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_GENERAL

    def test_ambiguous_keywords_go_to_simone(self):
        """If both coder and general keywords match, Simone triages."""
        result = qualify_agent(
            _task(title="Research and implement a new API endpoint"),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_SIMONE
        assert result["confidence"] == "default"

    def test_no_keywords_go_to_simone(self):
        result = qualify_agent(
            _task(title="Check on the status of things"),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_SIMONE
        assert result["confidence"] == "default"

    def test_description_keywords(self):
        """Keywords in description should also trigger routing."""
        result = qualify_agent(
            _task(
                title="Handle the task",
                description="Need to refactor the entire codebase",
            ),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_CODER


# ---------------------------------------------------------------------------
# Project-key routing
# ---------------------------------------------------------------------------

class TestProjectKeyRouting:

    def test_coding_project_key(self):
        result = qualify_agent(
            _task(title="Do some work", project_key="coding"),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_CODER

    def test_research_project_key(self):
        result = qualify_agent(
            _task(title="Do some work", project_key="research"),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_GENERAL

    def test_immediate_project_key_default(self):
        result = qualify_agent(
            _task(title="Do some work", project_key="immediate"),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_SIMONE


# ---------------------------------------------------------------------------
# Source-kind routing
# ---------------------------------------------------------------------------

class TestSourceKindRouting:

    def test_csi_source_routes_to_general(self):
        result = qualify_agent(
            _task(title="New signal detected", source_kind="csi"),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_GENERAL

    def test_signal_source_routes_to_general(self):
        result = qualify_agent(
            _task(title="Alert", source_kind="signal"),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_GENERAL


# ---------------------------------------------------------------------------
# Agent availability
# ---------------------------------------------------------------------------

class TestAgentAvailability:
    """When a target VP isn't available, fall back to Simone."""

    def test_coder_unavailable_falls_back(self):
        result = qualify_agent(
            _task(title="Fix the bug", labels=["code"]),
            available_agents=frozenset({AGENT_SIMONE}),  # Only Simone
        )
        # Should NOT route to coder since it's not available
        assert result["agent_id"] == AGENT_SIMONE

    def test_general_unavailable_falls_back(self):
        result = qualify_agent(
            _task(title="Research the topic", labels=["research"]),
            available_agents=frozenset({AGENT_SIMONE}),
        )
        assert result["agent_id"] == AGENT_SIMONE

    def test_coder_keyword_unavailable(self):
        """Keyword match to coder, but only Simone available."""
        result = qualify_agent(
            _task(title="Refactor the module"),
            available_agents=frozenset({AGENT_SIMONE, AGENT_GENERAL}),
        )
        # Coder keyword matches but coder not available → Simone
        assert result["agent_id"] == AGENT_SIMONE


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFeatureFlag:

    @patch.dict(os.environ, {"UA_AGENT_ROUTING_ENABLED": "1"})
    def test_enabled(self):
        assert _is_routing_enabled() is True

    @patch.dict(os.environ, {"UA_AGENT_ROUTING_ENABLED": "0"})
    def test_disabled(self):
        assert _is_routing_enabled() is False

    @patch.dict(os.environ, {}, clear=True)
    def test_default_disabled(self):
        assert _is_routing_enabled() is False


# ---------------------------------------------------------------------------
# Batch routing
# ---------------------------------------------------------------------------

class TestBatchRouting:

    @patch.dict(os.environ, {"UA_AGENT_ROUTING_ENABLED": "0"})
    def test_routing_disabled_all_to_simone(self):
        tasks = [
            _task(title="Fix bug", labels=["code"]),
            _task(title="Research topic", labels=["research"]),
        ]
        buckets = route_claimed_tasks(tasks, available_agents=ALL_AGENTS)
        assert AGENT_SIMONE in buckets
        assert len(buckets[AGENT_SIMONE]) == 2

    @patch.dict(os.environ, {"UA_AGENT_ROUTING_ENABLED": "1"})
    def test_routing_enabled_splits_tasks(self):
        tasks = [
            _task(title="Fix bug", labels=["code"]),
            _task(title="Research topic", labels=["research"]),
            _task(title="Send email update", labels=["email"]),
        ]
        buckets = route_claimed_tasks(tasks, available_agents=ALL_AGENTS)
        assert AGENT_CODER in buckets
        assert AGENT_GENERAL in buckets
        assert AGENT_SIMONE in buckets
        assert len(buckets[AGENT_CODER]) == 1
        assert len(buckets[AGENT_GENERAL]) == 1
        assert len(buckets[AGENT_SIMONE]) == 1

    @patch.dict(os.environ, {"UA_AGENT_ROUTING_ENABLED": "1"})
    def test_routing_enriches_tasks(self):
        tasks = [_task(title="Deploy app", labels=["deploy"])]
        route_claimed_tasks(tasks, available_agents=ALL_AGENTS)
        assert "_routing" in tasks[0]
        assert tasks[0]["_routing"]["agent_id"] == AGENT_CODER


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_task(self):
        result = qualify_agent({}, available_agents=ALL_AGENTS)
        assert result["agent_id"] == AGENT_SIMONE

    def test_labels_as_json_string(self):
        """Handle the case where labels come as a JSON string."""
        result = qualify_agent(
            {"labels": '["code", "testing"]', "title": ""},
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_CODER

    def test_case_insensitive_labels(self):
        result = qualify_agent(
            _task(title="Task", labels=["CODE"]),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_CODER

    def test_multiple_coder_labels(self):
        result = qualify_agent(
            _task(title="Task", labels=["code", "testing", "debug"]),
            available_agents=ALL_AGENTS,
        )
        assert result["agent_id"] == AGENT_CODER
