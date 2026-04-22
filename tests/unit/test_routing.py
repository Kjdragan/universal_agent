"""Tests for the declarative routing module."""

import os
import pytest

from universal_agent.routing import (
    ROUTE_SIMPLE,
    ROUTE_STANDARD,
    ROUTE_SYSTEM,
    classify_heuristic,
    is_system_intent,
    is_tool_required_intent,
    is_memory_intent,
    is_context_only_intent,
)


class TestClassifyHeuristic:
    """Core classification logic."""

    def test_system_heartbeat(self):
        route, label, tier = classify_heuristic("Run heartbeat_ok check")
        assert route == ROUTE_SYSTEM
        assert tier == "system"

    def test_system_read_heartbeat(self):
        route, label, tier = classify_heuristic("Read heartbeat and report status")
        assert route == ROUTE_SYSTEM

    def test_system_cron_job(self):
        route, label, tier = classify_heuristic("Run the cron job for cleanup")
        assert route == ROUTE_SYSTEM
        assert tier == "system"

    def test_system_scheduled_task(self):
        route, label, tier = classify_heuristic("Process scheduled task queue")
        assert route == ROUTE_SYSTEM

    def test_system_env_signal_heartbeat(self, monkeypatch):
        monkeypatch.setenv("UA_RUN_SOURCE", "heartbeat")
        route, label, tier = classify_heuristic("do something")
        assert route == ROUTE_SYSTEM
        assert tier == "system"

    def test_system_env_signal_cron(self, monkeypatch):
        monkeypatch.setenv("UA_RUN_SOURCE", "cron")
        route, label, tier = classify_heuristic("check the thing")
        assert route == ROUTE_SYSTEM

    def test_system_env_signal_not_set(self):
        os.environ.pop("UA_RUN_SOURCE", None)
        route, label, tier = classify_heuristic("hello there")
        assert route != ROUTE_SYSTEM

    def test_tool_attached_image(self):
        route, label, tier = classify_heuristic("Analyze [attached image: foo.png]")
        assert route == ROUTE_STANDARD
        assert tier == "tool_required"

    def test_tool_send_email(self):
        route, label, tier = classify_heuristic("Please send email to kevin")
        assert route == ROUTE_STANDARD

    def test_tool_search(self):
        route, label, tier = classify_heuristic("Search for latest AI news")
        assert route == ROUTE_STANDARD

    def test_tool_youtube(self):
        route, label, tier = classify_heuristic("Get transcript from https://youtube.com/watch?v=abc")
        assert route == ROUTE_STANDARD

    def test_tool_url_fetch(self):
        route, label, tier = classify_heuristic("Read https://example.com and summarize it")
        assert route == ROUTE_STANDARD
        assert tier == "tool_required"

    def test_tool_url_no_verb(self):
        route, label, tier = classify_heuristic("Check out https://example.com")
        # Should NOT match as url_fetch (no verb)
        if route == ROUTE_STANDARD:
            assert tier != "tool_required" or "url_fetch" not in label

    def test_memory_please_remember(self):
        route, label, tier = classify_heuristic("Please remember that I prefer dark mode")
        assert route == ROUTE_SIMPLE
        assert tier == "memory"

    def test_memory_my_name(self):
        route, label, tier = classify_heuristic("My name is Kevin")
        assert route == ROUTE_SIMPLE

    def test_context_filename(self):
        route, label, tier = classify_heuristic("What was the filename you just created?")
        assert route == ROUTE_SIMPLE
        assert tier == "context_only"

    def test_no_match_returns_empty(self):
        route, label, tier = classify_heuristic("Tell me about quantum computing")
        assert route == ""
        assert label == ""
        assert tier == ""
        return  # skip below
        route, label, tier = classify_heuristic("Tell me about quantum computing")
        assert route == ""
        assert label == ""

    def test_system_takes_priority_over_tool(self, monkeypatch):
        """System heuristics must be checked first."""
        monkeypatch.setenv("UA_RUN_SOURCE", "heartbeat")
        route, _, _ = classify_heuristic("send email to someone")
        assert route == ROUTE_SYSTEM

    def test_tool_takes_priority_over_memory(self):
        """Tool-required heuristics checked before memory."""
        route, _, _ = classify_heuristic("email me my preferences")
        assert route == ROUTE_STANDARD


class TestBackwardCompatWrappers:
    """Verify the old-style function signatures still work."""

    def test_is_system_intent(self):
        assert is_system_intent("Run cron job") is True
        assert is_system_intent("What's the weather?") is False

    def test_is_tool_required_intent(self):
        assert is_tool_required_intent("Search for X") is True
        assert is_tool_required_intent("What is 2+2?") is False

    def test_is_memory_intent(self):
        assert is_memory_intent("Please remember this") is True
        assert is_memory_intent("Tell me a joke") is False

    def test_is_context_only_intent(self):
        assert is_context_only_intent("What was the filename?") is True
        assert is_context_only_intent("What's up?") is False
