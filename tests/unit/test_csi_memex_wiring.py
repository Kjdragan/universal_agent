"""Tests for the Memex pass wired into ClaudeDevs replay (PR 15)."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from universal_agent.services import claude_code_intel_replay
from universal_agent.services.claude_code_intel_replay import (
    _memex_body_for_create,
    _memex_body_for_extend,
    _memex_candidates_for_action,
    apply_memex_pass,
    ingest_packet_into_external_vault,
)
from universal_agent.wiki.core import (
    ACTION_CREATE,
    ACTION_EXTEND,
    ensure_vault,
    memex_page_exists,
)


# ── Candidate extraction ────────────────────────────────────────────────────


def test_candidates_includes_release_info_package():
    action = {
        "post_id": "1",
        "text": "we shipped something",
        "release_info": {"package": "claude-agent-sdk", "version": "0.5.1"},
    }
    candidates = _memex_candidates_for_action(action)
    assert ("entity", "claude-agent-sdk") in candidates


def test_candidates_extracts_camelcase_terms_from_text():
    action = {
        "post_id": "1",
        "text": "Try the new MemoryTool with ManagedAgents support",
    }
    candidates = _memex_candidates_for_action(action)
    names = {n for _, n in candidates}
    assert "MemoryTool" in names
    assert "ManagedAgents" in names


def test_candidates_filters_stopwords():
    action = {
        "post_id": "1",
        "text": "Anthropic announced new Claude Tool support today",
    }
    candidates = _memex_candidates_for_action(action)
    names_lower = {n.lower() for _, n in candidates}
    # Generic words should not become entities.
    for stop in ("anthropic", "claude", "tool", "today"):
        assert stop not in names_lower


def test_candidates_caps_at_5_terms():
    action = {
        "post_id": "1",
        "text": "FeatureA FeatureB FeatureC FeatureD FeatureE FeatureF FeatureG FeatureH",
    }
    candidates = _memex_candidates_for_action(action)
    # 5 terms from text (cap), no release_info.
    assert len(candidates) == 5


def test_candidates_dedupe():
    action = {
        "post_id": "1",
        "text": "MemoryTool MemoryTool memorytool",  # case-insensitive dedupe
        "release_info": {"package": "claude-agent-sdk", "version": "0.5.1"},
    }
    candidates = _memex_candidates_for_action(action)
    # Only one MemoryTool entry plus the release package.
    names = [n.lower() for _, n in candidates]
    assert names.count("memorytool") == 1


def test_candidates_empty_when_nothing_matches():
    action = {"post_id": "1", "text": "just a chat with friends"}
    assert _memex_candidates_for_action(action) == []


# ── Body construction ───────────────────────────────────────────────────────


def test_create_body_includes_handle_post_url_text_reasoning_sources():
    action = {
        "post_id": "999",
        "text": "Skills support cross-project references",
        "url": "https://x.com/ClaudeDevs/status/999",
        "classifier": {"reasoning": "Reusable for agent systems"},
    }
    linked = [
        {"url": "https://docs.anthropic.com/skills", "title": "Skills docs"},
    ]
    body = _memex_body_for_create(handle="ClaudeDevs", action=action, linked_for_post=linked)
    assert "@ClaudeDevs" in body
    assert "https://x.com/ClaudeDevs/status/999" in body
    assert "post_id: `999`" in body
    assert "Skills support cross-project references" in body
    assert "Reusable for agent systems" in body
    assert "Skills docs" in body
    assert "https://docs.anthropic.com/skills" in body


def test_extend_body_is_compact():
    action = {"post_id": "999", "text": "small follow-up"}
    body = _memex_body_for_extend(action=action, linked_for_post=[])
    assert "small follow-up" in body
    # Extend bodies don't repeat the full discovery context block.
    assert "Discovery context" not in body


# ── End-to-end Memex pass ────────────────────────────────────────────────────


@pytest.fixture
def vault(tmp_path: Path):
    ctx = ensure_vault(
        "external",
        "memex-wiring-test",
        title="Memex Wiring Test",
        root_override=str(tmp_path / "vault"),
    )
    return ctx.path


def test_memex_pass_creates_entity_pages_from_release_info(vault: Path):
    actions = [
        {
            "post_id": "100",
            "text": "Claude Code 2.1.116 ships with Skills",
            "url": "https://x.com/ClaudeDevs/status/100",
            "release_info": {"package": "claude-code", "version": "2.1.116"},
            "action_type": "release_announcement",
        }
    ]
    results = apply_memex_pass(
        vault_path=vault,
        handle="ClaudeDevs",
        actions=actions,
        linked_source_entries=[],
    )
    # claude-code (release_info) + Skills (CamelCase) = 2 entities
    creates = [r for r in results if r.get("action") == ACTION_CREATE]
    names = {r["entity_name"] for r in creates}
    assert "claude-code" in names
    assert "Skills" in names
    assert memex_page_exists(vault, "entity", "claude-code")
    assert memex_page_exists(vault, "entity", "Skills")


def test_memex_pass_extends_existing_pages(vault: Path):
    """Second action that mentions an existing entity should EXTEND, not CREATE."""
    actions_first = [
        {
            "post_id": "100",
            "text": "MemoryTool announcement",
            "url": "https://x.com/ClaudeDevs/status/100",
        }
    ]
    apply_memex_pass(
        vault_path=vault,
        handle="ClaudeDevs",
        actions=actions_first,
        linked_source_entries=[],
    )
    # Now a second action mentioning the same entity.
    actions_second = [
        {
            "post_id": "200",
            "text": "MemoryTool now supports typed schemas",
            "url": "https://x.com/ClaudeDevs/status/200",
        }
    ]
    results = apply_memex_pass(
        vault_path=vault,
        handle="ClaudeDevs",
        actions=actions_second,
        linked_source_entries=[],
    )
    extends = [r for r in results if r.get("action") == ACTION_EXTEND and r.get("entity_name") == "MemoryTool"]
    assert len(extends) == 1
    # The page should have content from BOTH actions.
    page = (vault / "entities" / "memorytool.md").read_text(encoding="utf-8")
    assert "MemoryTool announcement" in page
    assert "MemoryTool now supports typed schemas" in page


def test_memex_pass_records_log_entries(vault: Path):
    actions = [
        {
            "post_id": "100",
            "text": "Hooks API expanded with PostToolUseFailure",
            "url": "https://x.com/ClaudeDevs/status/100",
        }
    ]
    apply_memex_pass(
        vault_path=vault,
        handle="ClaudeDevs",
        actions=actions,
        linked_source_entries=[],
    )
    log_text = (vault / "log.md").read_text(encoding="utf-8")
    assert "CREATE" in log_text
    # Log entries reference slugified page paths, e.g. entities/hooks.md.
    assert "entities/hooks.md" in log_text or "entities/posttoolusefailure.md" in log_text


def test_memex_pass_handles_per_action_failure_gracefully(vault: Path, monkeypatch):
    """One bad action must not block the rest."""

    actions = [
        {"post_id": "100", "text": "MemoryTool is great"},
        {"post_id": "200", "text": "Skills are awesome"},
    ]

    real_apply = claude_code_intel_replay.memex_apply_action
    call_count = {"n": 0}

    def flaky_apply(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated_first_call_failure")
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(claude_code_intel_replay, "memex_apply_action", flaky_apply)

    results = apply_memex_pass(
        vault_path=vault,
        handle="ClaudeDevs",
        actions=actions,
        linked_source_entries=[],
    )
    # First call errored — record in results.
    errors = [r for r in results if r.get("action") == "ERROR"]
    successes = [r for r in results if r.get("action") in (ACTION_CREATE, ACTION_EXTEND)]
    assert len(errors) >= 1
    assert len(successes) >= 1


def test_ingest_packet_runs_memex_when_wiring_enabled(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_CSI_MEMEX_WIRING_ENABLED", "1")
    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir()
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    (packet_dir / "manifest.json").write_text("{}", encoding="utf-8")

    posts = [{"id": "100", "text": "MemoryTool launched"}]
    actions = [{"post_id": "100", "text": "MemoryTool launched", "url": "https://x.com/ClaudeDevs/status/100"}]

    result = ingest_packet_into_external_vault(
        packet_dir=packet_dir,
        handle="ClaudeDevs",
        posts=posts,
        actions=actions,
        linked_source_entries=[],
        artifacts_root=artifacts_root,
        work_product_dir=None,
        enabled=True,
    )
    assert "memex_actions" in result
    memex_actions = result["memex_actions"]
    assert any(r.get("entity_name") == "MemoryTool" for r in memex_actions)


def test_ingest_packet_skips_memex_when_wiring_disabled(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_CSI_MEMEX_WIRING_ENABLED", "0")
    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir()
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    (packet_dir / "manifest.json").write_text("{}", encoding="utf-8")

    posts = [{"id": "100", "text": "MemoryTool launched"}]
    actions = [{"post_id": "100", "text": "MemoryTool launched"}]

    result = ingest_packet_into_external_vault(
        packet_dir=packet_dir,
        handle="ClaudeDevs",
        posts=posts,
        actions=actions,
        linked_source_entries=[],
        artifacts_root=artifacts_root,
        work_product_dir=None,
        enabled=True,
    )
    # memex_actions present but empty when wiring is off.
    assert result.get("memex_actions") == []


def test_ingest_packet_returns_memex_actions_field_when_disabled_via_enabled_flag(tmp_path: Path):
    """If ingest itself is disabled (enabled=False), nothing happens at all."""
    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir()
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    result = ingest_packet_into_external_vault(
        packet_dir=packet_dir,
        handle="ClaudeDevs",
        posts=[],
        actions=[],
        linked_source_entries=[],
        artifacts_root=artifacts_root,
        work_product_dir=None,
        enabled=False,
    )
    # Disabled-ingest short-circuit doesn't include memex_actions key.
    assert result == {"vault_path": "", "pages": [], "email_evidence_ids": []}
