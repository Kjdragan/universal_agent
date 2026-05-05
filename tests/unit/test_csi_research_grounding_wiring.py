"""Tests for the research grounding pass wired into ClaudeDevs replay (PR 16)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.services import claude_code_intel_replay
from universal_agent.services.claude_code_intel_replay import (
    _existing_entity_names,
    apply_research_grounding_pass,
)
from universal_agent.services.research_grounding import (
    ResearchRequest,
    ResearchResult,
    ResearchSource,
    TriggerReason,
)


# ── Vault entity discovery ───────────────────────────────────────────────────


def test_existing_entity_names_returns_lowercased_slugs(tmp_path: Path):
    entities = tmp_path / "entities"
    entities.mkdir()
    (entities / "memorytool.md").write_text("# MemoryTool\n", encoding="utf-8")
    (entities / "skills.md").write_text("# Skills\n", encoding="utf-8")
    names = _existing_entity_names(tmp_path)
    assert names == {"memorytool", "skills"}


def test_existing_entity_names_returns_empty_when_vault_missing(tmp_path: Path):
    """First-ever ingest must not crash because no vault exists yet."""
    names = _existing_entity_names(tmp_path / "nonexistent_vault")
    assert names == set()


# ── End-to-end research grounding pass with stubbed network ─────────────────


def _fake_research_result(post_id: str, urls: list[tuple[str, bool]]) -> ResearchResult:
    """Build a ResearchResult with sources pre-populated. urls = [(url, fetched)]."""
    request = ResearchRequest(
        post_id=post_id,
        tier=2,
        terms=("MemoryTool",),
        reasons=(TriggerReason.NO_LINKS,),
    )
    sources = tuple(
        ResearchSource(
            url=u,
            domain="docs.anthropic.com",
            allowlist_rank=0,
            fetched=fetched,
            content_path=f"/tmp/{u.replace('/', '_')}.md" if fetched else "",
            skip_reason="" if fetched else "stubbed_skip",
        )
        for u, fetched in urls
    )
    return ResearchResult(request=request, sources=sources)


def test_grounding_pass_fires_when_action_has_no_links(monkeypatch, tmp_path: Path):
    """Stub only execute_research; let the real build_research_request run.

    The build is pure-logic (no network) so we can exercise it directly.
    Stubbing both halves causes infinite recursion since build delegates
    via module-global reference.
    """

    def stub_execute(request, *, output_dir, timeout=20, lane=None, max_sources=6):
        return _fake_research_result(
            request.post_id,
            urls=[("https://docs.anthropic.com/en/docs/skills", True)],
        )

    monkeypatch.setattr("universal_agent.services.research_grounding.execute_research", stub_execute)

    actions = [
        {
            "post_id": "100",
            "tier": 2,
            "text": "MemoryTool launched",
            "links": [],  # no links — should trigger NO_LINKS
            "action_type": "kb_update",
        },
    ]
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    out = apply_research_grounding_pass(
        packet_dir=packet_dir,
        actions=actions,
        linked_source_entries=[],
        vault_root=tmp_path / "vault",
    )
    assert len(out) == 1
    assert out[0]["fetch_status"] == "fetched"
    assert out[0]["provenance_kind"] == "research_grounded"
    assert out[0]["allowlist_rank"] == 0


def test_grounding_pass_skips_tier_1_actions(monkeypatch, tmp_path: Path):
    """tier 1 = digest = noise; should never trigger research grounding."""
    called = {"n": 0}

    def stub_execute(request, *, output_dir, timeout=20, lane=None, max_sources=6):
        called["n"] += 1
        return _fake_research_result(request.post_id, [])

    monkeypatch.setattr("universal_agent.services.research_grounding.execute_research", stub_execute)

    actions = [
        {"post_id": "100", "tier": 1, "text": "FeatureTerm announced", "links": [], "action_type": "digest"},
    ]
    out = apply_research_grounding_pass(
        packet_dir=tmp_path,
        actions=actions,
        linked_source_entries=[],
        vault_root=tmp_path / "vault",
    )
    assert out == []
    assert called["n"] == 0


def test_grounding_pass_signals_thin_when_links_existed_but_none_fetched(monkeypatch, tmp_path: Path):
    captured: list[ResearchRequest] = []

    def stub_execute(request, *, output_dir, timeout=20, lane=None, max_sources=6):
        captured.append(request)
        return _fake_research_result(request.post_id, [])

    monkeypatch.setattr("universal_agent.services.research_grounding.execute_research", stub_execute)

    actions = [
        {
            "post_id": "100",
            "tier": 2,
            "text": "MemoryTool launched",
            "links": ["https://docs.anthropic.com/skills", "https://docs.anthropic.com/memory"],
            "action_type": "kb_update",
        },
    ]
    # Linked entries exist but none have fetch_status=fetched.
    linked_source_entries = [
        {"url": "https://docs.anthropic.com/skills", "post_id": "100", "fetch_status": "failed"},
        {"url": "https://docs.anthropic.com/memory", "post_id": "100", "fetch_status": "failed"},
    ]
    apply_research_grounding_pass(
        packet_dir=tmp_path,
        actions=actions,
        linked_source_entries=linked_source_entries,
        vault_root=tmp_path / "vault",
    )
    assert captured, "research request should have fired with THIN_LINKED_SOURCES"
    assert TriggerReason.THIN_LINKED_SOURCES in captured[0].reasons


def test_grounding_pass_uses_existing_entity_names_for_unknown_term_signal(monkeypatch, tmp_path: Path):
    captured: list[ResearchRequest] = []

    def stub_execute(request, *, output_dir, timeout=20, lane=None, max_sources=6):
        captured.append(request)
        return _fake_research_result(request.post_id, [])

    monkeypatch.setattr("universal_agent.services.research_grounding.execute_research", stub_execute)

    # Vault has 'skills' but not 'MemoryTool'.
    vault = tmp_path / "vault"
    (vault / "entities").mkdir(parents=True)
    (vault / "entities" / "skills.md").write_text("# Skills\n", encoding="utf-8")

    actions = [
        {
            "post_id": "100",
            "tier": 2,
            "text": "Skills now MemoryTool integration",  # MemoryTool is unknown, Skills is known
            "links": ["https://docs.anthropic.com/x"],  # have links → not NO_LINKS
            "action_type": "kb_update",
        },
    ]
    linked_source_entries = [
        {"url": "https://docs.anthropic.com/x", "post_id": "100", "fetch_status": "fetched"},
    ]
    apply_research_grounding_pass(
        packet_dir=tmp_path,
        actions=actions,
        linked_source_entries=linked_source_entries,
        vault_root=vault,
    )
    assert captured
    assert TriggerReason.UNKNOWN_TERM in captured[0].reasons


def test_grounding_pass_persists_research_grounding_json(monkeypatch, tmp_path: Path):
    def stub_execute(request, *, output_dir, timeout=20, lane=None, max_sources=6):
        return _fake_research_result(
            request.post_id,
            urls=[("https://docs.anthropic.com/x", True)],
        )

    monkeypatch.setattr("universal_agent.services.research_grounding.execute_research", stub_execute)

    actions = [
        {"post_id": "100", "tier": 2, "text": "MemoryTool launched", "links": [], "action_type": "kb_update"},
    ]
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    out = apply_research_grounding_pass(
        packet_dir=packet_dir,
        actions=actions,
        linked_source_entries=[],
        vault_root=tmp_path / "vault",
    )
    grounding_file = packet_dir / "research_grounding.json"
    assert grounding_file.exists()
    saved = json.loads(grounding_file.read_text(encoding="utf-8"))
    assert isinstance(saved, list)
    assert len(saved) == len(out)


def test_grounding_pass_handles_per_action_failure(monkeypatch, tmp_path: Path):
    """One failing execute_research call must not stop subsequent actions."""

    def stub_execute(request, *, output_dir, timeout=20, lane=None, max_sources=6):
        if request.post_id == "100":
            raise RuntimeError("simulated_failure")
        return _fake_research_result(
            request.post_id,
            urls=[("https://docs.anthropic.com/x", True)],
        )

    monkeypatch.setattr("universal_agent.services.research_grounding.execute_research", stub_execute)

    actions = [
        {"post_id": "100", "tier": 2, "text": "FeatureA launched", "links": [], "action_type": "kb_update"},
        {"post_id": "200", "tier": 2, "text": "FeatureB launched", "links": [], "action_type": "kb_update"},
    ]
    out = apply_research_grounding_pass(
        packet_dir=tmp_path,
        actions=actions,
        linked_source_entries=[],
        vault_root=tmp_path / "vault",
    )
    # Action 100 failed silently; action 200 still produced an entry.
    post_ids = {entry["post_id"] for entry in out}
    assert "200" in post_ids
    assert "100" not in post_ids


def test_grounding_emits_skipped_entries_with_reason(monkeypatch, tmp_path: Path):
    def stub_execute(request, *, output_dir, timeout=20, lane=None, max_sources=6):
        return _fake_research_result(
            request.post_id,
            urls=[("https://docs.anthropic.com/missing", False)],  # skipped, not fetched
        )

    monkeypatch.setattr("universal_agent.services.research_grounding.execute_research", stub_execute)

    actions = [
        {"post_id": "100", "tier": 2, "text": "MemoryTool launched", "links": [], "action_type": "kb_update"},
    ]
    out = apply_research_grounding_pass(
        packet_dir=tmp_path,
        actions=actions,
        linked_source_entries=[],
        vault_root=tmp_path / "vault",
    )
    assert len(out) == 1
    assert out[0]["fetch_status"] == "skipped"
    assert out[0]["skip_reason"] == "stubbed_skip"
