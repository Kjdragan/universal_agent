"""Tests for the P1 batched wiki facet extraction:
- ``wiki.llm.extract_facets_batched`` (two-tier batched calls, source_id mapping,
  heuristic fail-closed, correct model per tier);
- ``wiki.core.wiki_ingest_external_source`` honoring precomputed ``facets`` (no
  per-source LLM) and ``defer_index`` (skip the per-source index rebuild).
"""

from __future__ import annotations

import json

import pytest

from universal_agent.wiki import core as wiki_core, llm as wiki_llm


def _install_fake_call_llm(monkeypatch, *, record=None, raise_exc=None):
    """Patch llm_classifier._call_llm (the backend batched_judge lazily imports)
    with a deterministic fake that answers entities/concepts vs summary by system
    prompt and echoes each source's chunk-local index.

    Import the module and patch the OBJECT (not a dotted string) so resolution
    doesn't depend on whether a prior test already imported the submodule — a
    string target intermittently fails as 'no attribute' depending on test order.
    """
    import universal_agent.services.llm_classifier as llm_classifier

    async def fake_call_llm(*, system, user, max_tokens, **overrides):
        if record is not None:
            record.append({"system": system, "overrides": dict(overrides)})
        if raise_exc is not None:
            raise raise_exc
        payload = json.loads(user)
        srcs = payload["sources"]
        if "summariz" in system.lower() or "summary" in system.lower():
            verdicts = [{"index": s["index"], "summary": f"summary-{s['index']}"} for s in srcs]
        else:
            verdicts = [
                {"index": s["index"], "entities": [f"Ent{s['index']}"], "concepts": [f"Concept{s['index']}"]}
                for s in srcs
            ]
        return json.dumps({"verdicts": verdicts})

    monkeypatch.setattr(llm_classifier, "_call_llm", fake_call_llm)


def test_extract_facets_batched_maps_each_source(monkeypatch):
    _install_fake_call_llm(monkeypatch)
    sources = [
        {"source_id": "a", "text": "alpha text"},
        {"source_id": "b", "text": "beta text"},
        {"source_id": "c", "text": "gamma text"},
    ]
    out = wiki_llm.extract_facets_batched(sources)
    assert set(out) == {"a", "b", "c"}
    assert out["a"] == {"entities": ["Ent0"], "concepts": ["Concept0"], "summary": "summary-0"}
    assert out["b"]["summary"] == "summary-1"
    assert out["c"]["entities"] == ["Ent2"]


def test_extract_facets_batched_uses_two_model_tiers(monkeypatch):
    record = []
    _install_fake_call_llm(monkeypatch, record=record)
    wiki_llm.extract_facets_batched([{"source_id": "a", "text": "x"}])
    # Two calls: one extract (sonnet via _extract_model), one summary (opus).
    models = {("summary" if "summar" in r["system"].lower() else "extract"): r["overrides"].get("model") for r in record}
    assert models["extract"] == wiki_llm._extract_model()
    from universal_agent.utils.model_resolution import resolve_opus
    assert models["summary"] == resolve_opus()


def test_extract_facets_batched_fail_closed_to_heuristics(monkeypatch):
    _install_fake_call_llm(monkeypatch, raise_exc=RuntimeError("llm down, not fair-usage"))
    sources = [{"source_id": "a", "text": "Alpha Bravo discuss Charlie. Second sentence here."}]
    out = wiki_llm.extract_facets_batched(sources)
    # Entities fall back to the title-case heuristic; concepts to []; summary to
    # the first-sentence heuristic — i.e. identical to the legacy per-source path.
    assert out["a"]["entities"] == wiki_llm._heuristic_entities("Alpha Bravo discuss Charlie. Second sentence here.")
    assert out["a"]["concepts"] == []
    assert out["a"]["summary"] == wiki_llm._heuristic_summary("Alpha Bravo discuss Charlie. Second sentence here.")


def test_extract_facets_batched_empty_returns_empty(monkeypatch):
    _install_fake_call_llm(monkeypatch)
    assert wiki_llm.extract_facets_batched([]) == {}


def test_wiki_ingest_uses_precomputed_facets_without_llm(monkeypatch, tmp_path):
    # If the per-source extractors are reached, fail loudly — facets must short-circuit them.
    def _boom(*a, **k):
        raise AssertionError("per-source extractor must NOT run when facets are supplied")

    monkeypatch.setattr(wiki_core, "extract_entities", _boom)
    monkeypatch.setattr(wiki_core, "extract_concepts", _boom)
    monkeypatch.setattr(wiki_core, "generate_summary", _boom)

    out = wiki_core.wiki_ingest_external_source(
        vault_slug="testkb",
        source_title="Precomputed Source",
        source_content="body text",
        source_id="src-1",
        root_override=str(tmp_path / "vaults"),
        facets={"entities": ["Foo"], "concepts": ["Bar"], "summary": "precomputed summary"},
    )
    assert out["entities"] == ["Foo"]
    assert out["concepts"] == ["Bar"]
    assert out["summary"] == "precomputed summary"


def test_wiki_ingest_defer_index_skips_the_end_rebuild(monkeypatch, tmp_path):
    # ensure_vault rebuilds the index on EVERY call, so defer_index only skips the
    # ADDITIONAL end-of-ingest rebuild (the batch driver does one final rebuild
    # instead). Assert exactly that delta rather than an absolute count.
    counter = {"index": 0}
    monkeypatch.setattr(wiki_core, "update_index", lambda *a, **k: counter.__setitem__("index", counter["index"] + 1))
    monkeypatch.setattr(wiki_core, "refresh_overview", lambda *a, **k: None)
    facets = {"entities": [], "concepts": [], "summary": "s"}

    wiki_core.wiki_ingest_external_source(
        vault_slug="testkb", source_title="A", source_content="b", source_id="s1",
        root_override=str(tmp_path / "v"), facets=facets, defer_index=True,
    )
    deferred = counter["index"]

    counter["index"] = 0
    wiki_core.wiki_ingest_external_source(
        vault_slug="testkb", source_title="B", source_content="b", source_id="s2",
        root_override=str(tmp_path / "v"), facets=facets, defer_index=False,
    )
    not_deferred = counter["index"]

    assert not_deferred == deferred + 1  # the extra end-of-ingest rebuild we skip
