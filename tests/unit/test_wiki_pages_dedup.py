"""Regression for wiki_pages duplication in replay_summary.

Before this fix, multiple grounded sources sharing a domain (e.g.,
``docs.anthropic.com`` and ``docs.anthropic.com/en/release-notes/claude-code``)
had identical titles ``Grounded source (docs.anthropic.com)``. The vault's
slugifier truncates the title at 50 chars, so they collapsed to the same
on-disk filename — the second ingest silently overwrote the first, and the
``wiki_pages`` return value appended the same path twice (or more).

Two layers of fix:
1. Grounded-source titles now embed an 8-char URL hash so distinct URLs
   produce distinct slugs.
2. ``ingest_packet_into_external_vault`` dedupes the returned ``pages`` list
   so the operator never sees a misleading duplicate-count.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from universal_agent.services import claude_code_intel_replay as ccir


def _stub_ingest_returning_same_path():
    """Mimic the slug-collision behavior: every ingest collapses to one file."""

    def _impl(**kwargs):
        # Older slug behavior — all linked_source_* calls collapse to the same
        # filename because the title starts with the same 50-char prefix.
        if str(kwargs.get("source_id", "")).startswith("linked_source_"):
            return {"status": "success", "path": "sources/collision.md"}
        return {
            "status": "success",
            "path": f"sources/{kwargs.get('source_id') or 'misc'}.md",
        }

    return _impl


def test_wiki_pages_dedup_collapses_collision(monkeypatch, tmp_path):
    """When the slugifier collides, returned pages list is deduped."""
    monkeypatch.setattr(ccir, "wiki_ingest_external_source", _stub_ingest_returning_same_path())

    posts = [{"id": "p1", "text": "x"}]
    actions = [{"post_id": "p1", "tier": 4, "text": "y"}]
    linked = [
        {
            "url": f"https://docs.anthropic.com/page/{i}",
            "post_id": "p1",
            "tier": 4,
            "action_type": "kb_update",
            "fetch_status": "fetched",
            "source_path": str(tmp_path / f"src_{i}.md"),
            "title": "Grounded source (docs.anthropic.com)",
        }
        for i in range(4)
    ]
    for entry in linked:
        Path(entry["source_path"]).write_text("body", encoding="utf-8")

    result = ccir.ingest_packet_into_external_vault(
        packet_dir=tmp_path / "packet",
        handle="bcherny",
        posts=posts,
        actions=actions,
        linked_source_entries=linked,
        artifacts_root=tmp_path,
        work_product_dir=None,
        enabled=True,
    )

    # The 4 colliding linked sources should appear at most once in pages.
    collision_count = sum(
        1 for p in result["pages"] if p == "sources/collision.md"
    )
    assert collision_count <= 1, (
        f"wiki_pages contained duplicate paths: {result['pages']}"
    )


def test_grounded_source_titles_include_url_hash_for_disambiguation():
    """Two URLs sharing a domain must produce two distinct slug-friendly titles."""
    # Import the apply_research_grounding_pass machinery to introspect title
    # generation directly. We don't need to call the full pass — just verify
    # the title-construction line stays correct.
    src = Path("src/universal_agent/services/claude_code_intel_replay.py").read_text(
        encoding="utf-8"
    )
    # The fix marker: an 8-char hex hash is embedded in the title.
    assert 'hashlib.sha256(source.url.encode("utf-8")).hexdigest()[:8]' in src, (
        "grounded-source title must include URL hash for slug disambiguation"
    )
    assert '"title": f"Grounded source {url_hash} ({source.domain})"' in src, (
        "title format regressed; expected `Grounded source <hash> (<domain>)`"
    )
