"""Unit tests for the CSI vault-browser dashboard helpers.

Covers ``_claude_code_intel_knowledge_pages(categories=...)`` plus the
demo-walker ``_claude_code_intel_demos`` that the three new endpoints
under ``/api/v1/dashboard/claude-code-intel/vault/*`` and
``/api/v1/dashboard/claude-code-intel/demos`` delegate to.

The endpoints themselves are thin glue; testing the helpers directly
keeps these fast and avoids a FastAPI TestClient setup.
"""

from __future__ import annotations

import json
from pathlib import Path

from universal_agent import gateway_server
from universal_agent.wiki.core import _dump_markdown


def _write_page(
    path: Path,
    *,
    title: str,
    kind: str,
    tags: list[str],
    source_ids: list[str],
    confidence: str,
    summary: str,
    body_extra: str = "",
) -> None:
    meta = {
        "title": title,
        "kind": kind,
        "updated_at": "2026-05-10T12:00:00+00:00",
        "tags": tags,
        "source_ids": source_ids,
        "provenance_kind": "synthesis",
        "provenance_refs": [],
        "confidence": confidence,
        "status": "active",
        "summary": summary,
    }
    body = f"# {title}\n\n{summary}\n"
    if body_extra:
        body += "\n" + body_extra
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_markdown(meta, body), encoding="utf-8")


def test_knowledge_pages_entities_returns_enriched_fields(monkeypatch, tmp_path: Path):
    """Calling with categories=('entities',) yields kind/source_count/confidence/has_demos."""
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path)
    vault_root = tmp_path / "knowledge-vaults" / "claude-code-intelligence"

    _write_page(
        vault_root / "entities" / "custom-subagents.md",
        title="Custom Subagents",
        kind="feature",
        tags=["kind:feature", "anthropic-product"],
        source_ids=["s1", "s2", "s3"],
        confidence="high",
        summary="Anthropic's per-task subagent system.",
        body_extra="## Demos\n\n- `custom-subagents__demo-1` — workspace at /opt/ua_demos/custom-subagents__demo-1\n",
    )
    _write_page(
        vault_root / "entities" / "headless-mode.md",
        title="Headless Mode",
        kind="command",
        tags=["kind:command"],
        source_ids=["s4"],
        confidence="medium",
        summary="--headless flag for CLI invocations.",
    )

    result = gateway_server._claude_code_intel_knowledge_pages(categories=("entities",))
    assert len(result) == 2
    by_path = {r["path"]: r for r in result}

    sub = by_path["entities/custom-subagents.md"]
    assert sub["kind"] == "feature"
    assert sub["source_count"] == 3
    assert sub["confidence"] == "high"
    assert sub["has_demos"] is True
    assert sub["title"] == "Custom Subagents"
    assert "kind:feature" in sub["tags"]

    hl = by_path["entities/headless-mode.md"]
    assert hl["kind"] == "command"
    assert hl["source_count"] == 1
    assert hl["confidence"] == "medium"
    assert hl["has_demos"] is False


def test_knowledge_pages_concepts_filtered(monkeypatch, tmp_path: Path):
    """``categories=('concepts',)`` excludes entities and sources."""
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path)
    vault_root = tmp_path / "knowledge-vaults" / "claude-code-intelligence"

    _write_page(
        vault_root / "concepts" / "context-engineering.md",
        title="Context Engineering",
        kind="concept",
        tags=["kind:concept"],
        source_ids=["c1", "c2"],
        confidence="high",
        summary="Curating model context windows deliberately.",
    )
    _write_page(
        vault_root / "entities" / "something.md",
        title="Something",
        kind="feature",
        tags=["kind:feature"],
        source_ids=[],
        confidence="low",
        summary="An entity, should not appear.",
    )

    result = gateway_server._claude_code_intel_knowledge_pages(categories=("concepts",))
    assert len(result) == 1
    assert result[0]["path"] == "concepts/context-engineering.md"
    assert result[0]["kind"] == "concept"
    assert result[0]["source_count"] == 2


def test_knowledge_pages_sources_default_regression(monkeypatch, tmp_path: Path):
    """Default ``categories=('sources',)`` still works (backwards compat)."""
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path)
    vault_root = tmp_path / "knowledge-vaults" / "claude-code-intelligence"

    _write_page(
        vault_root / "sources" / "src-good.md",
        title="Good Source",
        kind="source",
        tags=["x.com"],
        source_ids=["x1"],
        confidence="medium",
        summary="A normal source page with real content.",
    )
    # Should be filtered out — JS-disabled X.com error capture.
    _write_page(
        vault_root / "sources" / "src-broken.md",
        title="Broken Source",
        kind="source",
        tags=["x.com", "twitter.com", "javascript"],
        source_ids=["x2"],
        confidence="low",
        summary="JavaScript is not available. Please enable JavaScript or switch to a supported browser.",
    )

    result = gateway_server._claude_code_intel_knowledge_pages()
    paths = [r["path"] for r in result]
    assert "sources/src-good.md" in paths
    assert "sources/src-broken.md" not in paths
    # Enriched fields still populated.
    good = next(r for r in result if r["path"] == "sources/src-good.md")
    assert good["source_count"] == 1
    assert good["confidence"] == "medium"
    assert good["has_demos"] is False


def test_demos_walker_returns_manifest_and_linkage(tmp_path: Path):
    """``_claude_code_intel_demos`` reads manifest.json and cross-references entity pages."""
    demos_root = tmp_path / "ua_demos"
    vault_root = tmp_path / "vault"
    entities_dir = vault_root / "entities"
    entities_dir.mkdir(parents=True)

    # Linked demo: entity page has ## Demos section mentioning the demo_id.
    (demos_root / "custom-subagents__demo-1").mkdir(parents=True)
    (demos_root / "custom-subagents__demo-1" / "manifest.json").write_text(
        json.dumps(
            {
                "feature": "custom subagents",
                "endpoint_hit": "anthropic_native",
                "marker_verified": True,
                "timestamp": "2026-05-12T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (entities_dir / "custom-subagents.md").write_text(
        "---\ntitle: Custom Subagents\n---\n\n"
        "# Custom Subagents\n\n"
        "Summary text.\n\n"
        "## Demos\n\n"
        "- `custom-subagents__demo-1` — built 2026-05-12\n",
        encoding="utf-8",
    )

    # Orphan demo: no entity page links it.
    (demos_root / "webhooks__demo-1").mkdir(parents=True)
    (demos_root / "webhooks__demo-1" / "manifest.json").write_text(
        json.dumps(
            {
                "feature": "webhooks",
                "endpoint_hit": "api.anthropic.com",
                "marker_verified": False,
                "timestamp": "2026-05-11T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    # Directory without a manifest — must be skipped.
    (demos_root / "scaffold-only").mkdir(parents=True)

    result = gateway_server._claude_code_intel_demos(
        demos_root=demos_root, vault_root=vault_root
    )
    assert len(result) == 2
    by_id = {d["demo_id"]: d for d in result}

    sub = by_id["custom-subagents__demo-1"]
    assert sub["feature"] == "custom subagents"
    assert sub["endpoint_hit"] == "anthropic_native"
    assert sub["marker_verified"] is True
    assert sub["entity_slug"] == "custom-subagents"
    assert sub["linked_from_entity"] is True
    assert sub["workspace_path"].endswith("custom-subagents__demo-1")

    wh = by_id["webhooks__demo-1"]
    assert wh["entity_slug"] == "webhooks"
    assert wh["linked_from_entity"] is False
    assert wh["marker_verified"] is False


def test_demos_walker_handles_missing_root(tmp_path: Path):
    """Non-existent demos_root returns empty list, not an error."""
    result = gateway_server._claude_code_intel_demos(
        demos_root=tmp_path / "does-not-exist",
        vault_root=tmp_path / "no-vault",
    )
    assert result == []
