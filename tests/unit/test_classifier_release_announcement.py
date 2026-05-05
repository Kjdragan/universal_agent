"""Verify classify_post recognizes release_announcement deterministically (PR 6a)."""

from __future__ import annotations

import os
from typing import Any

import pytest

from universal_agent.services.claude_code_intel import classify_post


@pytest.fixture(autouse=True)
def _disable_llm(monkeypatch):
    """Force heuristic-only classification so tests are deterministic."""
    monkeypatch.setenv("UA_CLAUDE_CODE_INTEL_LLM_CLASSIFIER_ENABLED", "0")


def _make_post(text: str, links: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": "1",
        "text": text,
        "created_at": "2026-05-05T00:00:00Z",
        "links": links or [],
    }


def test_release_announcement_detected_for_claude_code_version_in_text():
    post = _make_post("Claude Code 2.1.116 just shipped with skills support!")
    result = classify_post(post)
    assert result["action_type"] == "release_announcement"
    assert result["release_info"]["package"] == "claude-code"
    assert result["release_info"]["version"] == "2.1.116"
    assert result["release_info"]["is_anthropic_adjacent"] is True


def test_release_announcement_detected_via_link():
    post = _make_post(
        "New SDK release",
        links=["https://github.com/anthropics/claude-agent-sdk/releases/tag/v0.5.1"],
    )
    result = classify_post(post)
    assert result["action_type"] == "release_announcement"
    assert result["release_info"]["package"] == "claude-agent-sdk"


def test_release_announcement_floor_is_tier_2():
    """Even a thin release tweet must rank at least tier 2 — Phase 0 keys off it."""
    post = _make_post("anthropic 0.75.0")
    result = classify_post(post)
    assert result["tier"] >= 2


def test_non_release_post_does_not_get_release_info():
    post = _make_post("Some thoughts on agent design.")
    result = classify_post(post)
    assert result["action_type"] != "release_announcement"
    assert "release_info" not in result


def test_post_with_version_but_no_known_package_does_not_promote():
    """A version number on its own is not enough — we need a recognized package."""
    post = _make_post("Released 1.0.0 of our internal tool.")
    result = classify_post(post)
    assert result["action_type"] != "release_announcement"
