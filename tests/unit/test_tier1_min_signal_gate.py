"""Tests for the tier-1 vault-write minimum-signal gate.

Most tier-1 actions are conversational chatter ("Working on it!",
"Tell me more"). Writing a wiki page for each pollutes the vault index
without adding intelligence. The gate ensures tier-1 actions reach the
vault only when they carry at least one of: a link, a matched term, a
version string, or a signal-class content_kind classification.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from universal_agent.services import claude_code_intel_replay as ccir

# ── Unit tests for the predicate ────────────────────────────────────────────


def test_tier1_has_signal_links():
    assert ccir._tier1_has_signal({"links": ["https://example.com"], "text": "x"}) is True


def test_tier1_has_signal_matched_terms():
    assert ccir._tier1_has_signal({"matched_terms": ["tool"], "text": "x"}) is True


def test_tier1_has_signal_version_string():
    assert ccir._tier1_has_signal({"text": "shipped v2.1.5 of the SDK"}) is True
    assert ccir._tier1_has_signal({"text": "claude 4.6 released"}) is True


def test_tier1_has_signal_content_kind_allowlist():
    for kind in (
        "product_capability",
        "release_announcement",
        "usage_tip",
        "feature_announcement",
        "bug_fix",
        "migration_note",
    ):
        assert (
            ccir._tier1_has_signal(
                {"classifier": {"content_kind": kind}, "text": "x"}
            )
            is True
        ), f"content_kind={kind} should pass"


def test_tier1_has_signal_chatter_is_blocked():
    """Realistic chatter replies from the 2026-05-17 packet."""
    chatter_actions = [
        {"text": "@rajankrx Working on it!", "links": [], "matched_terms": []},
        {
            "text": "@downtownberlin @rajankrx Tell me more. Can you give me step by step walk through?",
            "links": [],
            "matched_terms": [],
        },
        {
            "text": "@nav7634 Let me see what we can do to improve this. Keep the feedback coming!",
            "links": [],
            "matched_terms": [],
            "classifier": {"content_kind": "community_event"},
        },
    ]
    for action in chatter_actions:
        assert ccir._tier1_has_signal(action) is False, action["text"]


def test_tier1_has_signal_treats_blank_strings_as_empty():
    assert ccir._tier1_has_signal({"links": ["  ", ""], "matched_terms": [""]}) is False


def test_tier1_has_signal_treats_empty_classifier_dict_safely():
    assert ccir._tier1_has_signal({"classifier": {}, "text": "chatter"}) is False


def test_tier1_has_signal_handles_non_dict_classifier():
    assert ccir._tier1_has_signal({"classifier": "garbage", "text": "chatter"}) is False


# ── Env toggle ──────────────────────────────────────────────────────────────


def test_tier1_min_signal_enabled_default_on(monkeypatch):
    monkeypatch.delenv("UA_CSI_TIER1_MIN_SIGNAL_ENABLED", raising=False)
    assert ccir._tier1_min_signal_enabled() is True


def test_tier1_min_signal_enabled_off_switch(monkeypatch):
    for value in ("0", "false", "FALSE", "off", "no"):
        monkeypatch.setenv("UA_CSI_TIER1_MIN_SIGNAL_ENABLED", value)
        assert ccir._tier1_min_signal_enabled() is False, value


# ── Integration with ingest_packet_into_external_vault ─────────────────────


def _stub_ingest(calls: list[dict[str, Any]]):
    def _impl(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "path": f"sources/{kwargs.get('source_id')}.md"}

    return _impl


def _vault_inputs(tmp_path: Path):
    """Minimal posts/actions structure with 5 tier-1 entries: 4 chatter + 1 signal."""
    posts = [
        {"id": f"p{i}", "text": "post " + str(i)} for i in range(5)
    ]
    actions = [
        # 1: pure chatter
        {"post_id": "p0", "tier": 1, "text": "Working on it!"},
        # 2: tier-1 with link → keeps
        {"post_id": "p1", "tier": 1, "text": "see this", "links": ["https://example.com"]},
        # 3: chatter with content_kind that should be filtered
        {
            "post_id": "p2",
            "tier": 1,
            "text": "Tell me more",
            "classifier": {"content_kind": "generic_update"},
        },
        # 4: tier-1 with version string → keeps
        {"post_id": "p3", "tier": 1, "text": "shipped v3.2.1"},
        # 5: tier-1 with signal content_kind → keeps
        {
            "post_id": "p4",
            "tier": 1,
            "text": "fixed it",
            "classifier": {"content_kind": "bug_fix"},
        },
    ]
    return posts, actions


def test_tier1_chatter_does_not_get_vault_pages(monkeypatch, tmp_path):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(ccir, "wiki_ingest_external_source", _stub_ingest(calls))
    posts, actions = _vault_inputs(tmp_path)

    result = ccir.ingest_packet_into_external_vault(
        packet_dir=tmp_path / "packet",
        handle="bcherny",
        posts=posts,
        actions=actions,
        linked_source_entries=[],
        artifacts_root=tmp_path,
        work_product_dir=None,
        enabled=True,
    )

    ingested_post_ids = {
        c.get("source_id") for c in calls if str(c.get("source_id", "")).startswith("x_post_")
    }
    # Chatter (p0, p2) suppressed; signal entries (p1, p3, p4) kept.
    assert ingested_post_ids == {"x_post_p1", "x_post_p3", "x_post_p4"}
    assert result["tier1_skipped_count"] == 2


def test_tier1_higher_tiers_always_pass(monkeypatch, tmp_path):
    """Tier-2+ entries are not subject to the gate even if they look like chatter."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(ccir, "wiki_ingest_external_source", _stub_ingest(calls))

    result = ccir.ingest_packet_into_external_vault(
        packet_dir=tmp_path / "packet",
        handle="bcherny",
        posts=[{"id": "p_hi", "text": "x"}],
        actions=[{"post_id": "p_hi", "tier": 3, "text": "ok"}],
        linked_source_entries=[],
        artifacts_root=tmp_path,
        work_product_dir=None,
        enabled=True,
    )

    assert any(c.get("source_id") == "x_post_p_hi" for c in calls)
    assert result["tier1_skipped_count"] == 0


def test_tier1_gate_can_be_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_CSI_TIER1_MIN_SIGNAL_ENABLED", "0")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(ccir, "wiki_ingest_external_source", _stub_ingest(calls))
    posts, actions = _vault_inputs(tmp_path)

    result = ccir.ingest_packet_into_external_vault(
        packet_dir=tmp_path / "packet",
        handle="bcherny",
        posts=posts,
        actions=actions,
        linked_source_entries=[],
        artifacts_root=tmp_path,
        work_product_dir=None,
        enabled=True,
    )

    ingested_post_ids = {
        c.get("source_id") for c in calls if str(c.get("source_id", "")).startswith("x_post_")
    }
    # Gate off → all five posts ingested.
    assert ingested_post_ids == {f"x_post_p{i}" for i in range(5)}
    assert result["tier1_skipped_count"] == 0


def test_tier1_gate_handles_missing_action(monkeypatch, tmp_path):
    """A post without a matching action defaults to tier=0 and is NOT subject to the gate."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(ccir, "wiki_ingest_external_source", _stub_ingest(calls))

    result = ccir.ingest_packet_into_external_vault(
        packet_dir=tmp_path / "packet",
        handle="bcherny",
        posts=[{"id": "p_unmatched", "text": "x"}],
        actions=[],
        linked_source_entries=[],
        artifacts_root=tmp_path,
        work_product_dir=None,
        enabled=True,
    )

    assert any(c.get("source_id") == "x_post_p_unmatched" for c in calls)
    assert result["tier1_skipped_count"] == 0
