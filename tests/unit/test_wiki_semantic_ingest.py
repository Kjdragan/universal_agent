"""RED tests for semantic ingest pipeline.

These tests verify that when LLM is available, the ingest pipeline produces
higher-quality entities, concepts, summaries, and page content than the
heuristic fallback.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from universal_agent.wiki import core
from universal_agent.wiki import llm as llm_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RICH_SOURCE_TEXT = """\
# Universal Agent Architecture

The Universal Agent (UA) is an autonomous AI system built on the Claude Agent SDK
that manages persistent sessions, handles email triage, orchestrates VP workers,
and maintains a durable state machine across heartbeat cycles.

## Key Components

### Claude Agent SDK Integration
UA uses the Claude Agent SDK to manage LLM interactions, tool registration,
and session lifecycle. The SDK provides a ClaudeSDKClient for transport
management and hook-based middleware.

### Heartbeat Loop
The heartbeat loop is a recurring execution cycle that checks for pending
tasks, processes email, updates memory, and triggers new agent sessions.
It runs on a configurable interval and includes built-in contention handling.

### NotebookLM Integration
NotebookLM provides supplementary research capabilities. It can generate
podcasts, study guides, and research summaries from ingested sources.
The integration is optional and derivative — not canonical.

### Obsidian Compatibility
All wiki output is designed to be Obsidian-compatible, using wikilinks,
YAML frontmatter, and local asset references for seamless graph viewing.
"""


@pytest.fixture
def vault_root(tmp_path):
    return str(tmp_path / "vaults")


@pytest.fixture
def source_file(tmp_path):
    source = tmp_path / "ua_architecture.md"
    source.write_text(RICH_SOURCE_TEXT, encoding="utf-8")
    return source


def _fake_entities():
    return [
        {"name": "Claude Agent SDK", "kind": "technology", "reason": "Core SDK used for LLM interactions"},
        {"name": "NotebookLM", "kind": "product", "reason": "Research tool integration"},
        {"name": "Universal Agent", "kind": "system", "reason": "The main autonomous AI system"},
    ]


def _fake_concepts():
    return [
        {"name": "heartbeat loop", "definition": "A recurring execution cycle that processes pending tasks and maintains system state.", "reason": "Core architectural pattern"},
        {"name": "durable state machine", "definition": "A persistent state management approach that survives agent restarts.", "reason": "Key reliability pattern"},
    ]


# ---------------------------------------------------------------------------
# Semantic ingest produces meaningful entities
# ---------------------------------------------------------------------------

class TestSemanticIngestEntities:
    def test_semantic_ingest_produces_meaningful_entities(self, vault_root, source_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        def fake_call(prompt, **kwargs):
            if "named entities" in prompt.lower():
                return json.dumps(_fake_entities())
            if "abstract concepts" in prompt.lower():
                return json.dumps(_fake_concepts())
            if "summary" in prompt.lower():
                return "The Universal Agent is an autonomous AI system that uses the Claude Agent SDK for session management and orchestrates VP workers through heartbeat cycles."
            if "description" in prompt.lower() or "definition" in prompt.lower():
                return "A sophisticated component of the Universal Agent architecture."
            return ""

        with patch.object(llm_mod, "_call_gemini", side_effect=fake_call):
            core.ensure_vault("external", "semantic-test", root_override=vault_root)
            result = core.ingest_external_source(
                vault_slug="semantic-test",
                source_path=str(source_file),
                title="UA Architecture",
                root_override=vault_root,
            )

        # Should have created entity pages
        assert len(result.get("entities", [])) > 0

        # Entity pages should exist and have LLM-generated content
        vault_path = Path(vault_root) / "semantic-test"
        for entity_path_rel in result["entities"]:
            entity_page = vault_path / entity_path_rel
            assert entity_page.exists(), f"Entity page not found: {entity_path_rel}"
            content = entity_page.read_text(encoding="utf-8")
            # Should NOT contain the generic placeholder text
            assert "Auto-maintained entity page" not in content


class TestSemanticIngestConcepts:
    def test_semantic_ingest_produces_meaningful_concepts(self, vault_root, source_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        def fake_call(prompt, **kwargs):
            if "named entities" in prompt.lower():
                return json.dumps(_fake_entities())
            if "abstract concepts" in prompt.lower():
                return json.dumps(_fake_concepts())
            if "summary" in prompt.lower():
                return "The Universal Agent manages persistent sessions and orchestrates VP workers."
            if "definition" in prompt.lower() or "description" in prompt.lower():
                return "An important architectural concept used in the Universal Agent."
            return ""

        with patch.object(llm_mod, "_call_gemini", side_effect=fake_call):
            core.ensure_vault("external", "concept-test", root_override=vault_root)
            result = core.ingest_external_source(
                vault_slug="concept-test",
                source_path=str(source_file),
                title="UA Architecture",
                root_override=vault_root,
            )

        # Should have created concept pages
        assert len(result.get("concepts", [])) > 0

        # Concept pages should have definitions
        vault_path = Path(vault_root) / "concept-test"
        for concept_path_rel in result["concepts"]:
            concept_page = vault_path / concept_path_rel
            assert concept_page.exists(), f"Concept page not found: {concept_path_rel}"
            content = concept_page.read_text(encoding="utf-8")
            assert "Auto-maintained concept page" not in content


class TestSemanticIngestSummary:
    def test_semantic_ingest_produces_semantic_summary(self, vault_root, source_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        llm_summary = "The Universal Agent is an autonomous AI system built on the Claude Agent SDK that manages persistent sessions, handles email triage, and maintains state through heartbeat cycles."

        def fake_call(prompt, **kwargs):
            if "named entities" in prompt.lower():
                return json.dumps([])
            if "abstract concepts" in prompt.lower():
                return json.dumps([])
            if "summary" in prompt.lower():
                return llm_summary
            return ""

        with patch.object(llm_mod, "_call_gemini", side_effect=fake_call):
            core.ensure_vault("external", "summary-test", root_override=vault_root)
            result = core.ingest_external_source(
                vault_slug="summary-test",
                source_path=str(source_file),
                title="UA Architecture",
                root_override=vault_root,
            )

        # The source page should contain the LLM summary
        vault_path = Path(vault_root) / "summary-test"
        source_page = vault_path / result["source_page"]
        content = source_page.read_text(encoding="utf-8")
        assert llm_summary in content


class TestHeuristicFallback:
    def test_heuristic_fallback_when_llm_unavailable(self, vault_root, source_file, monkeypatch):
        """When no API key is set, the ingest should still work using heuristics."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ZAI_API_KEY", raising=False)

        core.ensure_vault("external", "fallback-test", root_override=vault_root)
        result = core.ingest_external_source(
            vault_slug="fallback-test",
            source_path=str(source_file),
            title="UA Architecture",
            root_override=vault_root,
        )

        # Should still succeed
        assert result["status"] == "success"
        assert result["source_page"].startswith("sources/")

        # Vault should still be valid
        vault_path = Path(vault_root) / "fallback-test"
        assert (vault_path / "index.md").exists()


class TestEntityPageBodyHasSubstance:
    def test_entity_page_body_has_substance(self, vault_root, source_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        def fake_call(prompt, **kwargs):
            if "named entities" in prompt.lower():
                return json.dumps([
                    {"name": "Claude Agent SDK", "kind": "technology", "reason": "Core SDK"},
                ])
            if "abstract concepts" in prompt.lower():
                return json.dumps([])
            if "summary" in prompt.lower():
                return "A document about Universal Agent."
            if "description" in prompt.lower():
                return "The Claude Agent SDK is a Python library for managing LLM interactions, tool registration, and session lifecycle in autonomous agent systems."
            return ""

        with patch.object(llm_mod, "_call_gemini", side_effect=fake_call):
            core.ensure_vault("external", "substance-test", root_override=vault_root)
            result = core.ingest_external_source(
                vault_slug="substance-test",
                source_path=str(source_file),
                title="UA Architecture",
                root_override=vault_root,
            )

        vault_path = Path(vault_root) / "substance-test"
        for entity_path_rel in result["entities"]:
            entity_page = vault_path / entity_path_rel
            content = entity_page.read_text(encoding="utf-8")
            # Should have a description section with real content
            assert "Claude Agent SDK" in content
            # Body should have more than just a heading and a backlink
            body_lines = [l for l in content.split("---")[-1].splitlines() if l.strip()]
            assert len(body_lines) >= 3, f"Entity page body too thin: {body_lines}"
