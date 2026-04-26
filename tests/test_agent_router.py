"""Tests for the agent_router module — Simone-first multi-agent task qualification."""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent.services.agent_router import (
    AGENT_SIMONE,
    route_all_to_simone,
)
from universal_agent.vp.coder_runtime import CoderVPRuntime


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


# ---------------------------------------------------------------------------
# Tests for is_internal_system_request — hardened word-boundary markers
# ---------------------------------------------------------------------------


def _runtime() -> CoderVPRuntime:
    """Create a CoderVPRuntime with an in-memory DB for unit tests."""
    conn = sqlite3.connect(":memory:")
    return CoderVPRuntime(conn=conn, workspace_base="/tmp/test_coder_vp")


class TestIsInternalSystemRequest:
    """Verify that _INTERNAL_SYSTEM_MARKERS use word-boundary regex and
    no longer false-positive on common English words."""

    def test_matches_universal_agent_heartbeat_config(self):
        rt = _runtime()
        assert rt.is_internal_system_request("check the universal agent heartbeat config") is True

    def test_matches_simone_directly(self):
        rt = _runtime()
        assert rt.is_internal_system_request("ask simone to check the logs") is True

    def test_matches_mission_control(self):
        rt = _runtime()
        assert rt.is_internal_system_request("notify mission control about the deploy") is True

    def test_matches_system_configuration(self):
        rt = _runtime()
        assert rt.is_internal_system_request("update the system configuration for the agent") is True

    def test_matches_src_universal_agent_path(self):
        rt = _runtime()
        assert rt.is_internal_system_request("look at src/universal_agent/main.py") is True

    def test_no_match_add_calendar_event(self):
        """'calendar' was removed as a standalone marker -- it was too broad."""
        rt = _runtime()
        assert rt.is_internal_system_request("add a calendar event for tomorrow") is False

    def test_no_match_what_is_a_webhook(self):
        """'webhook' was removed as a standalone marker -- too common."""
        rt = _runtime()
        assert rt.is_internal_system_request("what is a webhook and how do I use one") is False

    def test_no_match_api_gateway_question(self):
        """'gateway' was removed as a standalone marker -- too common."""
        rt = _runtime()
        assert rt.is_internal_system_request("how does the API gateway work?") is False

    def test_no_match_hardcoded_desktop_path(self):
        """The old /home/kjdragan/lrepos/universal_agent path was removed."""
        rt = _runtime()
        assert rt.is_internal_system_request("/home/kjdragan/lrepos/universal_agent") is False

    def test_no_match_empty_string(self):
        rt = _runtime()
        assert rt.is_internal_system_request("") is False

    def test_no_match_none_input(self):
        rt = _runtime()
        assert rt.is_internal_system_request(None) is False

    def test_matches_heartbeat_word_boundary(self):
        """'heartbeat' as a word should still match."""
        rt = _runtime()
        assert rt.is_internal_system_request("run a heartbeat check") is True

    def test_matches_ops_config(self):
        rt = _runtime()
        assert rt.is_internal_system_request("update the ops config file") is True

    def test_matches_session_policy(self):
        rt = _runtime()
        assert rt.is_internal_system_request("check the session policy for this user") is True

    def test_case_insensitive(self):
        rt = _runtime()
        assert rt.is_internal_system_request("SIMONE should handle this") is True
        assert rt.is_internal_system_request("UNIVERSAL AGENT config update") is True
