"""Tests for the intel lanes config (PR 11 scaffolding)."""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.services.intel_lanes import (
    CLAUDE_CODE_LANE_KEY,
    LaneConfig,
    LanesDocument,
    all_lanes,
    enabled_lanes,
    get_lane,
    load_lanes_document,
    reset_cache,
)


def setup_function() -> None:
    """Each test starts with a clean cache so env/path overrides take effect."""
    reset_cache()


def test_default_document_parses_and_has_claude_code_lane():
    doc = load_lanes_document()
    assert isinstance(doc, LanesDocument)
    assert CLAUDE_CODE_LANE_KEY in doc.lanes


def test_default_claude_code_lane_has_required_fields():
    lane = get_lane(CLAUDE_CODE_LANE_KEY)
    assert lane.enabled is True
    assert lane.handles, "claude-code-intelligence must have at least one handle"
    assert "ClaudeDevs" in lane.handles
    assert lane.vault_slug == "claude-code-intelligence"
    assert lane.capability_library_slug == "claude_code_intel"
    assert lane.demo_endpoint_profile == "anthropic_native"
    assert any("docs.anthropic.com" in s for s in lane.research_allowlist)
    assert lane.cron_expr  # non-empty


def test_disabled_future_lanes_round_trip():
    doc = load_lanes_document()
    # Templates for future lanes ship disabled so they don't accidentally fire.
    for slug in ("openai-codex-intelligence", "gemini-intelligence"):
        if slug in doc.lanes:
            assert doc.lanes[slug].enabled is False


def test_enabled_lanes_only_returns_enabled():
    enabled = enabled_lanes()
    assert CLAUDE_CODE_LANE_KEY in enabled
    for slug, lane in enabled.items():
        assert lane.enabled is True


def test_all_lanes_returns_disabled_too():
    all_ = all_lanes()
    enabled = enabled_lanes()
    # all_lanes is a strict superset of enabled_lanes
    assert set(enabled.keys()).issubset(set(all_.keys()))


def test_get_lane_raises_for_unknown_slug():
    with pytest.raises(KeyError):
        get_lane("nonexistent-lane")


def test_handle_at_sign_stripping():
    lane = LaneConfig.model_validate(
        {
            "title": "test",
            "handles": ["@foo", "  @bar ", "baz"],
            "vault_slug": "x",
            "capability_library_slug": "x",
            "cron_expr": "0 0 * * *",
        }
    )
    assert lane.handles == ["foo", "bar", "baz"]


def test_unknown_top_level_keys_rejected(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "version: 1\n"
        "lanes:\n"
        "  test:\n"
        "    title: x\n"
        "    vault_slug: x\n"
        "    capability_library_slug: x\n"
        "    cron_expr: 0 0 * * *\n"
        "rogue_key: oops\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        load_lanes_document(bad)


def test_unknown_lane_keys_rejected(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "version: 1\n"
        "lanes:\n"
        "  test:\n"
        "    title: x\n"
        "    vault_slug: x\n"
        "    capability_library_slug: x\n"
        "    cron_expr: 0 0 * * *\n"
        "    rogue_key: oops\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        load_lanes_document(bad)


def test_explicit_path_override_works(tmp_path: Path):
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "version: 1\n"
        "lanes:\n"
        "  custom-lane:\n"
        "    title: Custom\n"
        "    handles: [Custom]\n"
        "    vault_slug: custom\n"
        "    capability_library_slug: custom\n"
        "    cron_expr: 0 0 * * *\n",
        encoding="utf-8",
    )
    doc = load_lanes_document(custom)
    assert "custom-lane" in doc.lanes
    assert doc.lanes["custom-lane"].title == "Custom"


def test_research_allowlist_priority_order_preserved():
    """Order matters — first entries are highest-trust."""
    lane = get_lane(CLAUDE_CODE_LANE_KEY)
    allowlist = lane.research_allowlist
    if "docs.anthropic.com" in allowlist and "anthropic.com/news" in allowlist:
        assert allowlist.index("docs.anthropic.com") < allowlist.index("anthropic.com/news"), (
            "official docs must outrank press releases"
        )
