"""Semantic extraction layer for LLM Wiki.

Extracts entities, concepts, and summaries using the standard Z.AI Anthropic 
emulation layer. Fails gracefully to heuristic extraction if the LLM is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Optional

from universal_agent.utils.model_resolution import resolve_opus, resolve_sonnet

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


def _extract_model() -> str:
    """Model tier for the bounded wiki EXTRACTION stages (entities / concepts) —
    sonnet (``glm-5-turbo``) by default, env-overridable via
    ``UA_WIKI_EXTRACT_MODEL``. Entity/concept extraction is a structured,
    bounded-output task; without an explicit model it fell through to
    ``_call_llm``'s ``resolve_opus()`` default (``glm-5.1`` — the flagship /
    most Fair-Usage-throttled tier) unnecessarily (observed burning real opus
    tokens on the ZAI token panel, 2026-06-13). Summary GENERATION keeps the
    opus default — it is generative and quality-sensitive."""
    return (os.getenv("UA_WIKI_EXTRACT_MODEL") or "").strip() or resolve_sonnet()


def _parse_json_list(raw: str, key: str = "items") -> list[str]:
    """Parse a JSON list of strings from the LLM response."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
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

# --- Heuristic fallbacks (shared by the per-source path and the batched
#     fail-closed path so both degrade identically) ---

def _heuristic_entities(text: str, fallback_limit: int = 5) -> list[str]:
    """Title-case word extraction — the entity fallback when the LLM is down."""
    words = re.findall(r'\b[A-Z][a-z]+\b', text or "")
    filtered = [w for w in set(words) if len(w) > 3]
    return filtered[:fallback_limit]


def _heuristic_summary(text: str) -> str:
    """First-sentence truncation — the summary fallback when the LLM is down."""
    stripped = (text or "").strip()
    if not stripped:
        return ""
    first_sentence = stripped.split('.')[0]
    if len(first_sentence) > 200:
        return first_sentence[:197] + "..."
    return first_sentence + "."


# --- Public API ---

def extract_entities(text: str, fallback_limit: int = 5) -> list[str]:
    """Extract named entities using LLM, with heuristic fallback."""
    if not text.strip():
        return []

    try:
        raw = _call_llm(
            system=_EXTRACT_ENTITIES_SYSTEM,
            user=f"Extract entities from:\n\n{text[:4000]}",
            model=_extract_model(),
        )
        entities = _parse_json_list(raw)
        return entities
    except Exception as exc:
        logger.warning(f"LLM entity extraction failed, using heuristic: {exc}")
        return _heuristic_entities(text, fallback_limit)


def extract_concepts(text: str, fallback_limit: int = 5) -> list[str]:
    """Extract abstract concepts using LLM, with heuristic fallback."""
    if not text.strip():
        return []

    try:
        raw = _call_llm(
            system=_EXTRACT_CONCEPTS_SYSTEM,
            user=f"Extract concepts from:\n\n{text[:4000]}",
            model=_extract_model(),
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
        return _heuristic_summary(text)


# --- Batched semantic extraction (P1: collapse the per-source 3-call fan-out) ---

_BATCHED_EXTRACT_SYSTEM = """\
You are an expert NLP system. You are given a JSON object with a "sources" array;
each source has an integer "index" and a "text". For EACH source, extract its most
important named entities (people, organizations, locations, products, specific
tools) and its most important abstract concepts/themes (1-3 words each). Exclude
generic words and stopwords.
Return ONLY JSON of the form:
{"verdicts":[{"index":<int>,"entities":["..."],"concepts":["..."]}]}
with EXACTLY one verdict per input source, keyed by that source's "index".
"""

_BATCHED_SUMMARY_SYSTEM = """\
You are a summarization assistant. You are given a JSON object with a "sources"
array; each source has an integer "index" and a "text". For EACH source, write a
concise 1-3 sentence summary capturing its core intent or factual content. No
introductory fluff.
Return ONLY JSON of the form:
{"verdicts":[{"index":<int>,"summary":"..."}]}
with EXACTLY one verdict per input source, keyed by that source's "index".
"""


def _wiki_extract_batch_size() -> int:
    """Sources judged per batched extraction call. Default 20 (the empirically
    best point from the convergence sweep, mirrored here); ``=1`` falls back to
    one call per source. ``UA_WIKI_EXTRACT_BATCH_SIZE`` overrides."""
    raw = str(os.getenv("UA_WIKI_EXTRACT_BATCH_SIZE", "20")).strip()
    try:
        return max(1, min(60, int(raw)))
    except ValueError:
        return 20


def _batched_sources_prompt(chunk: list[dict[str, Any]]) -> str:
    """One user payload for a chunk: each source carries its chunk-local index
    (the helper maps verdicts back by that index) and its text (truncated to the
    same 4000-char window the per-source path uses)."""
    return json.dumps(
        {"sources": [
            {"index": i, "text": str(s.get("text") or "")[:4000]}
            for i, s in enumerate(chunk)
        ]},
        ensure_ascii=True,
    )


async def _extract_facets_batched_async(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    from universal_agent.services.batched_judge import batched_judge

    batch_size = _wiki_extract_batch_size()

    # Tier 1 — entities + concepts on the EXTRACT model (sonnet by default; PR #987).
    ec_results = await batched_judge(
        sources,
        system=_BATCHED_EXTRACT_SYSTEM,
        build_prompt=_batched_sources_prompt,
        parse=lambda item, v: {
            "entities": [str(e) for e in (v.get("entities") or []) if isinstance(e, str)],
            "concepts": [str(c) for c in (v.get("concepts") or []) if isinstance(c, str)],
        },
        fail_closed=lambda item: {
            "entities": _heuristic_entities(str(item.get("text") or "")),
            "concepts": [],
        },
        batch_size=batch_size,
        model_overrides={"model": _extract_model()},
    )
    # Tier 2 — summary on OPUS (PR #987 deliberately keeps generation on opus);
    # batching still collapses these throttle-fragile opus CALLS.
    sum_results = await batched_judge(
        sources,
        system=_BATCHED_SUMMARY_SYSTEM,
        build_prompt=_batched_sources_prompt,
        parse=lambda item, v: str(v.get("summary") or "").strip(),
        fail_closed=lambda item: _heuristic_summary(str(item.get("text") or "")),
        batch_size=batch_size,
        model_overrides={"model": resolve_opus()},
    )

    out: dict[str, dict[str, Any]] = {}
    for src, ec, sm in zip(sources, ec_results, sum_results):
        sid = str(src.get("source_id") or "")
        if not sid:
            continue
        facets = dict(ec.value or {})
        facets.setdefault("entities", [])
        facets.setdefault("concepts", [])
        facets["summary"] = sm.value if isinstance(sm.value, str) else ""
        out[sid] = facets
    return out


def extract_facets_batched(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Batched semantic extraction for many sources at once.

    ``sources``: list of ``{"source_id": str, "text": str}``. Returns
    ``{source_id: {"entities": [...], "concepts": [...], "summary": str}}``.

    Collapses the per-source 3-call fan-out (``extract_entities`` +
    ``extract_concepts`` + ``generate_summary``: N sources → 3·N calls) into TWO
    batched structured calls per chunk of ``UA_WIKI_EXTRACT_BATCH_SIZE`` (default
    20): one sonnet call for ``{entities, concepts}`` and one opus call for
    ``{summary}`` → 3·N → 2·⌈N/20⌉ (~30× at N=20). Splitting the tiers keeps
    summary generation on its deliberate opus tier (PR #987) instead of silently
    downgrading it. Any per-source/per-facet failure falls back to the SAME
    heuristic the legacy per-source path uses, so a bad batch never drops a source.
    """
    if not sources:
        return {}
    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False
    if running:
        # Already inside an event loop (cannot asyncio.run here): degrade to the
        # per-source path — correct, just not batched. The CSI replay chain is
        # synchronous today, so this is a defensive fallback, not the hot path.
        return {
            str(s.get("source_id") or ""): {
                "entities": extract_entities(str(s.get("text") or "")),
                "concepts": extract_concepts(str(s.get("text") or "")),
                "summary": generate_summary(str(s.get("text") or "")),
            }
            for s in sources
            if str(s.get("source_id") or "")
        }
    return asyncio.run(_extract_facets_batched_async(sources))
