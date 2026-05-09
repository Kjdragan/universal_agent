"""Unit tests for briefings_agent.py (Phase 2 Lane A wiring, P2.A3).

Covers the pure helpers extracted from main():
  - _get_hn_block_or_empty: kill-switch behavior + delegates to build_briefing_block
  - _build_objective: prompt assembly with/without the HN block

The async main() itself isn't unit-tested — its deps (httpx, gateway,
dispatch_vp_mission) are integration-level. The helpers below are the
parts that determine briefing quality and need test coverage.
"""
from __future__ import annotations

import pytest

from universal_agent.scripts import briefings_agent

# ─── _get_hn_block_or_empty (kill-switch) ──────────────────────────────


def test_get_hn_block_returns_empty_when_kill_switch_enabled(monkeypatch) -> None:
    monkeypatch.setenv("UA_HACKERNEWS_BRIEFING_BLOCK_ENABLED", "0")

    def boom(*args, **kwargs):  # noqa: ARG001 — should never be called
        raise RuntimeError("build_briefing_block must NOT be called when kill switch is set")

    monkeypatch.setattr(briefings_agent, "build_briefing_block", boom)

    assert briefings_agent._get_hn_block_or_empty(["claude"]) == ""


def test_get_hn_block_calls_builder_when_kill_switch_unset(monkeypatch) -> None:
    monkeypatch.delenv("UA_HACKERNEWS_BRIEFING_BLOCK_ENABLED", raising=False)

    captured: dict = {}

    def fake(watchlist, **kwargs):  # noqa: ARG001
        captured["watchlist"] = watchlist
        return "FAKE BLOCK"

    monkeypatch.setattr(briefings_agent, "build_briefing_block", fake)

    out = briefings_agent._get_hn_block_or_empty(["claude", "agent"])
    assert out == "FAKE BLOCK"
    assert captured["watchlist"] == ["claude", "agent"]


def test_get_hn_block_calls_builder_when_kill_switch_truthy(monkeypatch) -> None:
    """Kill switch is OFF unless explicitly set to '0'."""
    monkeypatch.setenv("UA_HACKERNEWS_BRIEFING_BLOCK_ENABLED", "1")
    monkeypatch.setattr(briefings_agent, "build_briefing_block", lambda watchlist, **_: "OK")
    assert briefings_agent._get_hn_block_or_empty(["claude"]) == "OK"


def test_get_hn_block_swallows_helper_exceptions(monkeypatch) -> None:
    """If the helper raises, the briefing must proceed without HN context."""
    monkeypatch.delenv("UA_HACKERNEWS_BRIEFING_BLOCK_ENABLED", raising=False)

    def boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("helper crashed")

    monkeypatch.setattr(briefings_agent, "build_briefing_block", boom)
    assert briefings_agent._get_hn_block_or_empty(["claude"]) == ""


# ─── _build_objective (prompt assembly) ────────────────────────────────


def test_build_objective_includes_telemetry_and_wiki_when_present() -> None:
    out = briefings_agent._build_objective(
        telemetry_json='{"x": 1}',
        wiki_content="# Wiki content here",
        hn_block="",
        artifacts_dir="/opt/artifacts",
        today="2026-05-09",
    )
    assert '"x": 1' in out
    assert "Wiki content here" in out
    assert "/opt/artifacts/autonomous-briefings/2026-05-09/DAILY_BRIEFING.md" in out


def test_build_objective_omits_hn_section_when_block_empty() -> None:
    out = briefings_agent._build_objective(
        telemetry_json="{}",
        wiki_content="",
        hn_block="",
        artifacts_dir="/x",
        today="2026-05-09",
    )
    assert "Hacker News This Week" not in out
    assert "webReader" not in out


def test_build_objective_includes_hn_block_and_atlas_instructions() -> None:
    block = "## Hacker News This Week (test block)\n\nSome content"
    out = briefings_agent._build_objective(
        telemetry_json="{}",
        wiki_content="",
        hn_block=block,
        artifacts_dir="/x",
        today="2026-05-09",
    )
    assert block in out
    # Atlas-side tool-use instruction must appear when the block is non-empty
    assert "webReader" in out
    assert "webSearchPrime" in out
    # And the LLM should know "nothing relevant today" is OK
    assert "Nothing relevant" in out or "nothing in the HN block" in out.lower()


def test_build_objective_preserves_existing_instructions() -> None:
    """The existing pre-Phase-2 instructions (telemetry summary, wiki refs) must remain."""
    out = briefings_agent._build_objective(
        telemetry_json="{}",
        wiki_content="",
        hn_block="",
        artifacts_dir="/x",
        today="2026-05-09",
    )
    assert "Latest Proactive Knowledge Base Additions" in out
    assert "tasks completed, attempted, and failed" in out


# ─── public API ────────────────────────────────────────────────────────


@pytest.mark.parametrize("public_name", [
    "_get_hn_block_or_empty",
    "_build_objective",
    "build_briefing_block",
])
def test_briefings_agent_exposes_helpers(public_name: str) -> None:
    assert hasattr(briefings_agent, public_name), f"briefings_agent must expose {public_name}"
