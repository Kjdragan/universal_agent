"""Semantic extraction layer for LLM Wiki.

Extracts entities, concepts, and summaries using the standard Z.AI Anthropic 
emulation layer. Fails gracefully to heuristic extraction if the LLM is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from universal_agent.utils.model_resolution import resolve_opus

logger = logging.getLogger(__name__)


class SemanticExtractionError(Exception):
    """Raised when semantic extraction fails."""


def _get_anthropic_client():
    """Create a synchronous Anthropic client using the ZAI emulation layer."""
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise SemanticExtractionError("anthropic package not installed") from exc

    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        raise SemanticExtractionError("No Anthropic API key available")

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url

    return Anthropic(**client_kwargs)


def _call_llm(
    *,
    system: str,
    user: str,
    model: Optional[str] = None,
    max_tokens: int = 2048,  # doubled from 1024 per audit
) -> str:
    """Make a synchronous LLM call and return the raw text response."""
    client = _get_anthropic_client()

    response = client.messages.create(
        model=model or resolve_opus(),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    raw_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            raw_text += block.text

    return raw_text.strip()


def _parse_json_list(raw: str, key: str = "items") -> list[str]:
    """Parse a JSON list of strings from the LLM response."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if isinstance(item, str)]
        elif isinstance(parsed, dict) and key in parsed:
            val = parsed[key]
            if isinstance(val, list):
                return [str(item) for item in val if isinstance(item, str)]
        return []
    except Exception:
        return []

# --- System Prompts ---

_EXTRACT_ENTITIES_SYSTEM = """\
You are an expert NLP system. Given a text, extract the most important named entities 
(people, organizations, locations, products, specific tools).
Return ONLY a JSON array of strings, e.g. ["OpenAI", "Alice", "Project X"].
Exclude generic words or extremely common stopwords.
"""

_EXTRACT_CONCEPTS_SYSTEM = """\
You are an expert NLP system. Given a text, extract the most important abstract 
concepts, themes, or technical ideas discussed.
Return ONLY a JSON array of strings, e.g. ["Machine Learning", "Workflow Automation", "Memory Management"].
Keep concepts concise (1-3 words).
"""

_GENERATE_SUMMARY_SYSTEM = """\
You are a summarization assistant. Provide a concise, 1-3 sentence summary of the 
provided text, capturing the core intent or factual content. Do not include introductory fluff.
"""

# --- Public API ---

def extract_entities(text: str, fallback_limit: int = 5) -> list[str]:
    """Extract named entities using LLM, with heuristic fallback."""
    if not text.strip():
        return []
        
    try:
        raw = _call_llm(
            system=_EXTRACT_ENTITIES_SYSTEM, 
            user=f"Extract entities from:\n\n{text[:4000]}"
        )
        entities = _parse_json_list(raw)
        return entities
    except Exception as exc:
        logger.warning(f"LLM entity extraction failed, using heuristic: {exc}")
        # Very simple title-case word extraction as heuristic fallback
        words = re.findall(r'\b[A-Z][a-z]+\b', text)
        filtered = [w for w in set(words) if len(w) > 3]
        return filtered[:fallback_limit]


def extract_concepts(text: str, fallback_limit: int = 5) -> list[str]:
    """Extract abstract concepts using LLM, with heuristic fallback."""
    if not text.strip():
        return []
        
    try:
        raw = _call_llm(
            system=_EXTRACT_CONCEPTS_SYSTEM, 
            user=f"Extract concepts from:\n\n{text[:4000]}"
        )
        concepts = _parse_json_list(raw)
        return concepts
    except Exception as exc:
        logger.warning(f"LLM concept extraction failed, using heuristic: {exc}")
        return []


def generate_summary(text: str) -> str:
    """Generate a short summary using LLM, with heuristic fallback."""
    if not text.strip():
        return ""
        
    try:
        raw = _call_llm(
            system=_GENERATE_SUMMARY_SYSTEM, 
            user=f"Summarize this text:\n\n{text[:4000]}"
        )
        return raw.strip()
    except Exception as exc:
        logger.warning(f"LLM summarization failed, using heuristic: {exc}")
        stripped = text.strip()
        if not stripped:
            return ""
        first_sentence = stripped.split('.')[0]
        if len(first_sentence) > 200:
            return first_sentence[:197] + "..."
        return first_sentence + "."
