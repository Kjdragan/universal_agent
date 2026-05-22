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


# ─── _get_triage_block_or_empty (CSI demo triage) ──────────────────────


def test_get_triage_block_returns_empty_when_kill_switch_enabled(monkeypatch) -> None:
    monkeypatch.setenv("UA_TRIAGE_BRIEFING_BLOCK_ENABLED", "0")

    # If the import is attempted at all, the kill-switch failed.
    def boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("triage helpers must NOT be called when kill switch is set")

    monkeypatch.setattr("universal_agent.services.csi_demo_triage.open_db", boom, raising=False)
    assert briefings_agent._get_triage_block_or_empty() == ""


def test_get_triage_block_returns_empty_when_no_pending(monkeypatch) -> None:
    monkeypatch.delenv("UA_TRIAGE_BRIEFING_BLOCK_ENABLED", raising=False)

    from universal_agent.services import csi_demo_triage as triage

    monkeypatch.setattr(triage, "open_db", lambda artifacts_root=None: _FakeConn())
    monkeypatch.setattr(triage, "get_counts", lambda **_: {"pending": 0, "approved": 0, "dismissed": 0})
    monkeypatch.setattr(triage, "get_top_recommendations", lambda **_: [])
    monkeypatch.setattr(triage, "list_candidates", lambda **_: [])

    assert briefings_agent._get_triage_block_or_empty() == ""


def test_get_triage_block_swallows_helper_exceptions(monkeypatch) -> None:
    monkeypatch.delenv("UA_TRIAGE_BRIEFING_BLOCK_ENABLED", raising=False)
    from universal_agent.services import csi_demo_triage as triage

    def boom(*a, **k):
        raise RuntimeError("db not ready")

    monkeypatch.setattr(triage, "open_db", boom)
    assert briefings_agent._get_triage_block_or_empty() == ""


def test_get_triage_block_renders_when_pending(monkeypatch) -> None:
    monkeypatch.delenv("UA_TRIAGE_BRIEFING_BLOCK_ENABLED", raising=False)
    from universal_agent.services import csi_demo_triage as triage

    class _Cand:
        def __init__(self, first_seen_at: str) -> None:
            self.first_seen_at = first_seen_at

        def to_dict(self) -> dict:
            return {
                "post_id": "1",
                "post_url": "https://x.com/ClaudeDevs/status/1",
                "post_text": "ship /ultrareview",
                "ranking_score": 0.92,
            }

    monkeypatch.setattr(triage, "open_db", lambda artifacts_root=None: _FakeConn())
    monkeypatch.setattr(triage, "get_counts", lambda **_: {"pending": 3, "approved": 0, "dismissed": 0})
    monkeypatch.setattr(triage, "get_top_recommendations", lambda **_: [_Cand("2026-05-10T00:00:00Z")])
    monkeypatch.setattr(
        triage,
        "list_candidates",
        lambda **_: [_Cand("2026-05-10T00:00:00Z"), _Cand("2026-05-12T00:00:00Z")],
    )

    out = briefings_agent._get_triage_block_or_empty()
    assert "Pending tier-3 candidates" in out
    assert "**3**" in out
    assert "/ultrareview" in out
    assert "Demo Triage" in out


# ─── _build_triage_block (rendering) ───────────────────────────────────


def test_build_triage_block_singular_day() -> None:
    out = briefings_agent._build_triage_block(pending=1, top=[], oldest_days=1)
    assert "1 day ago" in out
    assert "**1**" in out


def test_build_triage_block_handles_unknown_age() -> None:
    out = briefings_agent._build_triage_block(pending=5, top=[], oldest_days=None)
    assert "**5**" in out
    # No malformed " day ago" trailing artifact.
    assert "day ago" not in out


# ─── _build_objective (triage splice) ──────────────────────────────────


def test_build_objective_omits_triage_section_when_block_empty() -> None:
    out = briefings_agent._build_objective(
        telemetry_json="{}",
        wiki_content="",
        hn_block="",
        triage_block="",
        artifacts_dir="/x",
        today="2026-05-15",
    )
    assert "Demo Triage" not in out
    assert "Operator Decision" not in out


def test_build_objective_includes_triage_block_when_present() -> None:
    block = "## Claude Code Demo Triage — Operator Decision Needed\n\n- Pending: 5"
    out = briefings_agent._build_objective(
        telemetry_json="{}",
        wiki_content="",
        hn_block="",
        triage_block=block,
        artifacts_dir="/x",
        today="2026-05-15",
    )
    assert block in out
    # Instructions must mention the queue-depth callout
    assert "demo-triage" in out.lower()


# ─── public API ────────────────────────────────────────────────────────


@pytest.mark.parametrize("public_name", [
    "_get_hn_block_or_empty",
    "_get_triage_block_or_empty",
    "_build_triage_block",
    "_build_objective",
    "_get_atlas_briefs_block_or_empty",
    "_build_atlas_briefs_block",
    "build_briefing_block",
])
def test_briefings_agent_exposes_helpers(public_name: str) -> None:
    assert hasattr(briefings_agent, public_name), f"briefings_agent must expose {public_name}"


# Fake context-manager-friendly connection used by the triage helpers.
class _FakeConn:
    def close(self) -> None:
        pass


# ─── _build_atlas_briefs_block (pure renderer) ─────────────────────────


def test_build_atlas_briefs_block_renders_title_and_summary() -> None:
    briefs = [
        {"title": "ATLAS insight brief: Tech Labor Bifurcation", "summary": "Two-class market emerging."},
        {"title": "ATLAS insight brief: Open-Source AI Fragility", "summary": "Corporate forks dominate."},
    ]
    out = briefings_agent._build_atlas_briefs_block(briefs)
    assert "ATLAS Insight Briefs" in out
    assert "New since last briefing: **2**" in out
    # Title prefix is stripped.
    assert "**Tech Labor Bifurcation**" in out
    assert "Two-class market emerging." in out
    # No bare "ATLAS insight brief:" prefix in the user-facing list.
    assert "**ATLAS insight brief: Tech" not in out


def test_build_atlas_briefs_block_handles_missing_summary() -> None:
    briefs = [{"title": "ATLAS insight brief: lonely", "summary": ""}]
    out = briefings_agent._build_atlas_briefs_block(briefs)
    assert "**lonely**" in out
    # Should not leave a trailing em-dash for an empty summary.
    assert "**lonely** —" not in out


# ─── _get_atlas_briefs_block_or_empty (kill switch + DB integration) ──


def test_get_atlas_briefs_block_returns_empty_when_kill_switch_enabled(monkeypatch) -> None:
    monkeypatch.setenv("UA_ATLAS_BRIEFING_BLOCK_ENABLED", "0")
    # If kill switch fires, the helper must short-circuit before any import.
    assert briefings_agent._get_atlas_briefs_block_or_empty() == ""


def test_get_atlas_briefs_block_swallows_import_errors(monkeypatch) -> None:
    """Any failure in the helper must NEVER break the briefing."""
    monkeypatch.delenv("UA_ATLAS_BRIEFING_BLOCK_ENABLED", raising=False)

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated DB failure")

    # Patching the imported helper directly inside the function would
    # require sys.modules surgery; simpler: monkeypatch the DB connector
    # so the helper raises during open.
    import universal_agent.durable.db as _db

    monkeypatch.setattr(_db, "connect_runtime_db", boom)
    assert briefings_agent._get_atlas_briefs_block_or_empty() == ""


def test_build_objective_includes_atlas_section_when_block_present() -> None:
    atlas = "## ATLAS Insight Briefs — Awaiting Operator Triage\n\n- New since last briefing: **3**"
    out = briefings_agent._build_objective(
        telemetry_json="{}",
        wiki_content="",
        hn_block="",
        artifacts_dir="/x",
        today="2026-05-22",
        atlas_block=atlas,
    )
    assert atlas in out
    # Instruction line for Atlas synthesis appears when the block is non-empty.
    assert "ATLAS Insight Briefs" in out
    assert "cross-cutting theme" in out or "ATLAS pass" in out


def test_build_objective_omits_atlas_section_when_block_empty() -> None:
    out = briefings_agent._build_objective(
        telemetry_json="{}",
        wiki_content="",
        hn_block="",
        artifacts_dir="/x",
        today="2026-05-22",
        atlas_block="",
    )
    assert "ATLAS Insight Briefs" not in out
    # Atlas-specific instruction prose must NOT leak into prompts that
    # have no Atlas block (avoids the LLM hallucinating an empty section).
    assert "cross-cutting theme" not in out
