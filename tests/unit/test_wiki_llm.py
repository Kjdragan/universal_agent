"""RED tests for wiki/llm.py — the LLM integration layer.

These tests mock the Gemini client to verify:
1. Structured output schemas for entity/concept extraction
2. Summary generation returns strings
3. Graceful fallback when no API key
4. Graceful fallback on timeout/error
5. Input text truncation to stay within fast-response territory
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_gemini_response(text: str) -> MagicMock:
    """Build a fake Gemini response object with a .text attribute."""
    resp = MagicMock()
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# is_llm_available
# ---------------------------------------------------------------------------

class TestIsLlmAvailable:
    def test_returns_true_when_key_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        from universal_agent.wiki.llm import is_llm_available
        assert is_llm_available() is True

    def test_returns_true_when_google_key_set(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.setenv("ZAI_API_KEY", "test-google-key")
        from universal_agent.wiki.llm import is_llm_available
        assert is_llm_available() is True

    def test_returns_false_when_no_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        from universal_agent.wiki.llm import is_llm_available
        assert is_llm_available() is False


# ---------------------------------------------------------------------------
# extract_entities_llm
# ---------------------------------------------------------------------------

class TestExtractEntitiesLlm:
    def test_returns_structured_entity_list(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        from universal_agent.wiki import llm as llm_mod

        fake_entities = [
            {"name": "OpenAI Codex", "kind": "product", "reason": "AI coding assistant referenced as key tool"},
            {"name": "Universal Agent", "kind": "system", "reason": "The main agent system described"},
        ]
        mock_resp = _mock_gemini_response(json.dumps(fake_entities))

        with patch.object(llm_mod, "_call_gemini", return_value=mock_resp.text):
            result = llm_mod.extract_entities_llm("OpenAI Codex helps Universal Agent maintain knowledge.", "Test Doc")

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["name"] == "OpenAI Codex"
        assert "kind" in result[0]
        assert "reason" in result[0]

    def test_returns_empty_on_no_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        from universal_agent.wiki import llm as llm_mod

        result = llm_mod.extract_entities_llm("Some text", "Title")
        assert result == []

    def test_returns_empty_on_error(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        from universal_agent.wiki import llm as llm_mod

        with patch.object(llm_mod, "_call_gemini", side_effect=Exception("API error")):
            result = llm_mod.extract_entities_llm("Some text", "Title")
        assert result == []


# ---------------------------------------------------------------------------
# extract_concepts_llm
# ---------------------------------------------------------------------------

class TestExtractConceptsLlm:
    def test_returns_structured_concept_list(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        from universal_agent.wiki import llm as llm_mod

        fake_concepts = [
            {"name": "knowledge management", "definition": "Systematic approach to capturing organizational knowledge", "reason": "Core theme of the document"},
            {"name": "semantic extraction", "definition": "Using NLP to identify meaning from text", "reason": "Key technique described"},
        ]
        mock_resp = _mock_gemini_response(json.dumps(fake_concepts))

        with patch.object(llm_mod, "_call_gemini", return_value=mock_resp.text):
            result = llm_mod.extract_concepts_llm("Knowledge management using semantic extraction.", "Test Concepts")

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["name"] == "knowledge management"
        assert "definition" in result[0]

    def test_returns_empty_on_no_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        from universal_agent.wiki import llm as llm_mod

        result = llm_mod.extract_concepts_llm("Some text", "Title")
        assert result == []


# ---------------------------------------------------------------------------
# generate_summary_llm
# ---------------------------------------------------------------------------

class TestGenerateSummaryLlm:
    def test_returns_summary_string(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        from universal_agent.wiki import llm as llm_mod

        summary = "This document describes how Universal Agent maintains persistent knowledge bases using LLM-driven wiki pages."
        with patch.object(llm_mod, "_call_gemini", return_value=summary):
            result = llm_mod.generate_summary_llm("Long document content here...", "Knowledge Base Doc")

        assert isinstance(result, str)
        assert len(result) > 20
        assert "knowledge" in result.lower()

    def test_returns_empty_on_no_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        from universal_agent.wiki import llm as llm_mod

        result = llm_mod.generate_summary_llm("Some text", "Title")
        assert result == ""

    def test_returns_empty_on_error(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        from universal_agent.wiki import llm as llm_mod

        with patch.object(llm_mod, "_call_gemini", side_effect=Exception("timeout")):
            result = llm_mod.generate_summary_llm("Some text", "Title")
        assert result == ""


# ---------------------------------------------------------------------------
# generate_entity_description_llm
# ---------------------------------------------------------------------------

class TestGenerateEntityDescriptionLlm:
    def test_returns_description_string(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        from universal_agent.wiki import llm as llm_mod

        desc = "OpenAI Codex is an AI-powered coding assistant that translates natural language into code."
        with patch.object(llm_mod, "_call_gemini", return_value=desc):
            result = llm_mod.generate_entity_description_llm("OpenAI Codex", ["Codex helps maintain knowledge bases."])

        assert isinstance(result, str)
        assert len(result) > 10

    def test_returns_empty_on_no_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        from universal_agent.wiki import llm as llm_mod

        result = llm_mod.generate_entity_description_llm("Entity", ["some context"])
        assert result == ""


# ---------------------------------------------------------------------------
# generate_concept_description_llm
# ---------------------------------------------------------------------------

class TestGenerateConceptDescriptionLlm:
    def test_returns_description_string(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        from universal_agent.wiki import llm as llm_mod

        desc = "Knowledge management is the systematic process of creating, sharing, using, and managing the knowledge within an organization."
        with patch.object(llm_mod, "_call_gemini", return_value=desc):
            result = llm_mod.generate_concept_description_llm("knowledge management", ["Managing organizational knowledge effectively."])

        assert isinstance(result, str)
        assert len(result) > 10

    def test_returns_empty_on_no_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        from universal_agent.wiki import llm as llm_mod

        result = llm_mod.generate_concept_description_llm("concept", ["some context"])
        assert result == ""


# ---------------------------------------------------------------------------
# Text truncation
# ---------------------------------------------------------------------------

class TestTextTruncation:
    def test_input_is_truncated(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        from universal_agent.wiki import llm as llm_mod

        long_text = "word " * 2000  # ~10000 chars
        captured_prompt = []

        def fake_call(prompt: str, **kwargs) -> str:
            captured_prompt.append(prompt)
            return "[]"

        with patch.object(llm_mod, "_call_gemini", side_effect=fake_call):
            llm_mod.extract_entities_llm(long_text, "Long Doc")

        # The prompt should contain truncated text, not the full 10k chars
        assert len(captured_prompt) == 1
        # The source text in the prompt should be capped (we allow some overhead for the prompt template)
        assert len(captured_prompt[0]) < 6000  # 4000 char cap + prompt template overhead


# ---------------------------------------------------------------------------
# compile_ledger_llm
# ---------------------------------------------------------------------------

class TestCompileLedgerLlm:
    def test_returns_synthesized_markdown(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        from universal_agent.wiki import llm as llm_mod

        synthesized = "## Key Decisions\n\n1. **Adopted wiki pattern** — chose markdown-native knowledge base over vector DB.\n2. **Immutable raw sources** — raw sources are never modified after ingest."
        with patch.object(llm_mod, "_call_gemini", return_value=synthesized):
            result = llm_mod.compile_ledger_llm("decisions", ["decided to use wiki pattern", "raw sources immutable"])

        assert isinstance(result, str)
        assert len(result) > 20

    def test_returns_empty_on_no_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        from universal_agent.wiki import llm as llm_mod

        result = llm_mod.compile_ledger_llm("decisions", ["some evidence"])
        assert result == ""
