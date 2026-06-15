"""Graph materialization in wiki_ingest_external_source (Spec A).

Hermetic: `facets` is supplied so no entity/concept/summary LLM call fires, and
`generate_summary` is patched as a belt-and-suspenders guard against the per-page
summary call hidden in `_scan_page_records`.
"""
from __future__ import annotations

from unittest.mock import patch

from universal_agent.wiki.core import lint_vault, wiki_ingest_external_source

SOURCE_CONTENT = (
    "GLM-5 and Kimi K2.7 advance agentic engineering with sparse attention "
    "and asynchronous reinforcement learning."
)
FACETS = {
    "entities": ["GLM-5", "Kimi K2.7", "Zhipu AI"],
    "concepts": ["Agentic Engineering", "Sparse Attention"],
    "summary": "A briefing on open-weights coding models.",
}


@patch("universal_agent.wiki.core.generate_summary", return_value="")
def test_materialize_creates_linked_entity_and_concept_pages(_summary, tmp_path):
    result = wiki_ingest_external_source(
        vault_slug="glm-kimi",
        source_title="GLM-5 vs Kimi K2.7",
        source_content=SOURCE_CONTENT,
        source_id="src-1",
        root_override=str(tmp_path),
        facets=FACETS,
        materialize_graph=True,
    )

    assert result["status"] == "success"
    graph = result["graph"]
    assert graph["entities_materialized"] == 3
    assert graph["concepts_materialized"] == 2
    assert graph["pages_created"] == 5
    assert graph["pages_extended"] == 0

    vault = tmp_path / "glm-kimi"
    assert (vault / "entities" / "glm-5.md").exists()
    assert (vault / "entities" / "kimi-k2-7.md").exists()
    assert (vault / "entities" / "zhipu-ai.md").exists()
    assert (vault / "concepts" / "agentic-engineering.md").exists()
    assert (vault / "concepts" / "sparse-attention.md").exists()

    # Source page links OUT to the entity/concept pages.
    src = (vault / result["path"]).read_text()
    assert "## Entities" in src
    assert "## Concepts" in src
    assert "[[entities/glm-5.md|GLM-5]]" in src
    assert "[[concepts/agentic-engineering.md|Agentic Engineering]]" in src

    # Entity page backlinks the source (within-vault edge, both directions).
    ent = (vault / "entities" / "glm-5.md").read_text()
    assert "## Sources" in ent
    assert "[[sources/" in ent

    # Materialized pages are indexed.
    index = (vault / "index.md").read_text()
    assert "entities/glm-5.md" in index
    assert "concepts/sparse-attention.md" in index

    # The graph is well-formed: no broken wikilinks, no orphan pages.
    lint = lint_vault(vault_kind="external", vault_slug="glm-kimi", root_override=str(tmp_path))
    broken = [f for f in lint["findings"] if f["kind"] == "broken_wikilink"]
    orphans = [f for f in lint["findings"] if f["kind"] == "orphan_page"]
    assert broken == [], broken
    assert orphans == [], orphans


@patch("universal_agent.wiki.core.generate_summary", return_value="")
def test_reingest_extends_not_duplicates(_summary, tmp_path):
    common = dict(
        vault_slug="v",
        root_override=str(tmp_path),
        facets=FACETS,
        materialize_graph=True,
    )
    wiki_ingest_external_source(
        source_title="First", source_content=SOURCE_CONTENT, source_id="s1", **common
    )
    second = wiki_ingest_external_source(
        source_title="Second", source_content=SOURCE_CONTENT, source_id="s2", **common
    )

    # Same terms the second night -> extend existing pages, create none.
    assert second["graph"]["pages_created"] == 0
    assert second["graph"]["pages_extended"] == 5

    vault = tmp_path / "v"
    assert len(list((vault / "entities").glob("*.md"))) == 3
    assert len(list((vault / "concepts").glob("*.md"))) == 2

    # The shared entity page now backlinks BOTH sources.
    ent = (vault / "entities" / "glm-5.md").read_text()
    assert ent.count("[[sources/") >= 2


@patch("universal_agent.wiki.core.generate_summary", return_value="")
def test_materialize_disabled_writes_seed_only(_summary, tmp_path):
    result = wiki_ingest_external_source(
        vault_slug="seed",
        source_title="Seed only",
        source_content=SOURCE_CONTENT,
        source_id="src",
        root_override=str(tmp_path),
        facets=FACETS,
        materialize_graph=False,
    )
    assert "graph" not in result
    vault = tmp_path / "seed"
    assert list((vault / "entities").glob("*.md")) == []
    assert list((vault / "concepts").glob("*.md")) == []
