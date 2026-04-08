"""LLM integration layer for the wiki engine.

Uses the Z.AI Anthropic emulation layer (same as llm_classifier, decomposition_agent,
etc.) for semantic reasoning tasks:
- Entity/concept extraction from source text
- Summary generation
- Entity/concept page description generation
- Internal memory ledger synthesis

All functions are synchronous and have graceful fallbacks when no API key
is available or when the LLM call fails. This keeps the wiki engine usable
in CI, tests, and offline environments.

Design principle: LLM for *reasoning*, Python for *plumbing*.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Maximum source text chars sent to the LLM. Keeps responses fast and cheap.
MAX_SOURCE_CHARS = 4000

# Model tier — use sonnet-equivalent for wiki extraction (cost-effective routine tasks)
MODEL_TIER = "sonnet"


# ---------------------------------------------------------------------------
# Client management — mirrors services/llm_classifier.py pattern
# ---------------------------------------------------------------------------

def _get_api_key() -> str | None:
    """Return the Anthropic/ZAI API key from environment, or None."""
    return (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or os.environ.get("ZAI_API_KEY")
        or None
    )


def is_llm_available() -> bool:
    """Check whether an LLM API key is configured."""
    return _get_api_key() is not None


def _get_model() -> str:
    """Resolve the model identifier via the project's model_resolution utility."""
    from universal_agent.utils.model_resolution import resolve_model
    return resolve_model(MODEL_TIER)


def _call_gemini(prompt: str, *, json_mode: bool = False) -> str:
    """Make a synchronous Anthropic-compatible LLM call and return the response text.

    Despite the name (kept for test compatibility), this uses the ZAI
    Anthropic emulation layer — the same provider used by llm_classifier,
    decomposition_agent, and other project services.

    This is the single choke-point for all LLM calls in the wiki engine.
    All other functions call this, making it easy to mock in tests.

    Raises on failure — callers are expected to catch and degrade gracefully.
    """
    from anthropic import Anthropic

    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("No Anthropic/ZAI API key available")

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url

    client = Anthropic(**client_kwargs)

    model = _get_model()

    system_prompt = "You are a knowledge-base assistant that extracts structured information from documents."
    if json_mode:
        system_prompt += " Always respond with valid JSON only. No markdown fencing, no explanation text."

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            raw_text += block.text

    return raw_text.strip()


def _truncate(text: str, max_chars: int = MAX_SOURCE_CHARS) -> str:
    """Truncate text to max_chars, appending a note if truncated."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n\n[Truncated for analysis]"


def _parse_json_response(raw: str) -> Any:
    """Parse JSON from LLM response, stripping markdown fencing if present."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    return json.loads(cleaned)


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

def extract_entities_llm(text: str, title: str) -> list[dict[str, str]]:
    """Extract named entities worth creating wiki pages for.

    Returns a list of dicts with keys: name, kind, reason.
    Returns [] on failure or when LLM is unavailable.
    """
    if not is_llm_available():
        return []

    source_text = _truncate(text)
    prompt = f"""Analyze this document and extract the most important named entities that deserve their own wiki page in a knowledge base.

Document title: {title}

Document text:
{source_text}

For each entity, provide:
- "name": The proper name of the entity (e.g., "OpenAI Codex", "Claude", "Universal Agent")
- "kind": The entity type (e.g., "person", "organization", "product", "system", "technology", "standard")
- "reason": A brief explanation of why this entity is noteworthy in context (1 sentence)

Rules:
- Only include entities that are specific and notable — not generic terms
- Maximum 5 entities
- Exclude common stopwords and generic labels like "Agent", "System", "Summary"
- Prefer entities that would be useful cross-references in a knowledge base

Return a JSON array of objects. If no notable entities found, return an empty array [].
"""
    try:
        raw = _call_gemini(prompt, json_mode=True)
        parsed = _parse_json_response(raw)
        if not isinstance(parsed, list):
            return []
        # Validate structure
        result = []
        for item in parsed[:5]:
            if isinstance(item, dict) and "name" in item:
                result.append({
                    "name": str(item.get("name", "")),
                    "kind": str(item.get("kind", "entity")),
                    "reason": str(item.get("reason", "")),
                })
        return result
    except Exception:
        logger.debug("extract_entities_llm failed, returning empty", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Concept extraction
# ---------------------------------------------------------------------------

def extract_concepts_llm(text: str, title: str) -> list[dict[str, str]]:
    """Extract abstract concepts worth creating wiki pages for.

    Returns a list of dicts with keys: name, definition, reason.
    Returns [] on failure or when LLM is unavailable.
    """
    if not is_llm_available():
        return []

    source_text = _truncate(text)
    prompt = f"""Analyze this document and extract the most important abstract concepts that deserve their own wiki page in a knowledge base.

Document title: {title}

Document text:
{source_text}

For each concept, provide:
- "name": The concept name in lowercase (e.g., "knowledge management", "semantic extraction", "immutable storage")
- "definition": A concise 1-2 sentence definition of the concept
- "reason": Why this concept is important in the context of this document (1 sentence)

Rules:
- Only include concepts that are meaningful and specific — not generic words
- These should be ideas, techniques, patterns, or principles — not named entities
- Maximum 5 concepts
- Exclude trivial or obvious terms
- Focus on concepts that connect ideas across multiple documents

Return a JSON array of objects. If no notable concepts found, return an empty array [].
"""
    try:
        raw = _call_gemini(prompt, json_mode=True)
        parsed = _parse_json_response(raw)
        if not isinstance(parsed, list):
            return []
        result = []
        for item in parsed[:5]:
            if isinstance(item, dict) and "name" in item:
                result.append({
                    "name": str(item.get("name", "")),
                    "definition": str(item.get("definition", "")),
                    "reason": str(item.get("reason", "")),
                })
        return result
    except Exception:
        logger.debug("extract_concepts_llm failed, returning empty", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------

def generate_summary_llm(text: str, title: str) -> str:
    """Generate a 2-3 sentence semantic summary of a source document.

    Returns "" on failure or when LLM is unavailable.
    """
    if not is_llm_available():
        return ""

    source_text = _truncate(text)
    prompt = f"""Write a concise 2-3 sentence summary of this document for a knowledge base index page. Focus on the key insights and what makes this document worth referencing.

Document title: {title}

Document text:
{source_text}

Return only the summary text, no formatting or labels.
"""
    try:
        return _call_gemini(prompt).strip()
    except Exception:
        logger.debug("generate_summary_llm failed, returning empty", exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# Entity/concept page description generation
# ---------------------------------------------------------------------------

def generate_entity_description_llm(entity_name: str, source_excerpts: list[str]) -> str:
    """Generate a meaningful description for an entity wiki page.

    Returns "" on failure or when LLM is unavailable.
    """
    if not is_llm_available():
        return ""

    context = "\n".join(f"- {excerpt[:500]}" for excerpt in source_excerpts[:5])
    prompt = f"""Write a brief 2-3 sentence description of "{entity_name}" based on the following context from source documents. This will appear on a wiki page about this entity.

Context from sources:
{context}

Write a factual, informative description. Do not use phrases like "Based on the context" or "According to the sources". Just describe the entity directly.
"""
    try:
        return _call_gemini(prompt).strip()
    except Exception:
        logger.debug("generate_entity_description_llm failed", exc_info=True)
        return ""


def generate_concept_description_llm(concept_name: str, source_excerpts: list[str]) -> str:
    """Generate a meaningful description for a concept wiki page.

    Returns "" on failure or when LLM is unavailable.
    """
    if not is_llm_available():
        return ""

    context = "\n".join(f"- {excerpt[:500]}" for excerpt in source_excerpts[:5])
    prompt = f"""Write a brief 2-3 sentence definition and explanation of the concept "{concept_name}" based on the following context from source documents. This will appear on a wiki page about this concept.

Context from sources:
{context}

Write a clear, informative definition. Do not use phrases like "Based on the context" or "According to the sources". Define the concept directly.
"""
    try:
        return _call_gemini(prompt).strip()
    except Exception:
        logger.debug("generate_concept_description_llm failed", exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# Internal memory ledger synthesis
# ---------------------------------------------------------------------------

def compile_ledger_llm(category: str, evidence_lines: list[str]) -> str:
    """Synthesize raw evidence lines into a structured markdown ledger.

    Used for decision, preference, incident, and other internal memory ledgers.
    Returns "" on failure or when LLM is unavailable.
    """
    if not is_llm_available():
        return ""

    if not evidence_lines:
        return ""

    evidence = "\n".join(f"- {line[:300]}" for line in evidence_lines[:30])
    prompt = f"""Synthesize the following raw evidence from agent memory into a well-organized markdown section for a "{category}" ledger page. Group related items, remove duplicates, and present the information clearly.

Category: {category}

Raw evidence:
{evidence}

Rules:
- Use markdown headers (##) for logical groupings
- Use numbered lists for ordered items, bullet lists for unordered
- Be concise but preserve all unique information
- Do not add information not present in the evidence
- Output only the markdown content, no surrounding explanation
"""
    try:
        return _call_gemini(prompt).strip()
    except Exception:
        logger.debug("compile_ledger_llm failed", exc_info=True)
        return ""
