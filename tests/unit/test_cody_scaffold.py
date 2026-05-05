"""Tests for the Phase 2 scaffold builder (PR 8)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from universal_agent.services.cody_scaffold import (
    ScaffoldArtifacts,
    VaultEntity,
    build_demo_scaffold,
    find_vault_entity,
    populate_workspace_sources,
    read_entity,
    select_relevant_sources,
    write_acceptance_template,
    write_brief_template,
    write_business_relevance_template,
)


# ── Vault entity loading ────────────────────────────────────────────────────


def _write_entity(vault_root: Path, slug: str, frontmatter: dict, body: str) -> Path:
    entities = vault_root / "entities"
    entities.mkdir(parents=True, exist_ok=True)
    path = entities / f"{slug}.md"
    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    path.write_text(f"---\n{fm_yaml}\n---\n\n{body}\n", encoding="utf-8")
    return path


def test_find_vault_entity_by_slug(tmp_path: Path):
    vault = tmp_path / "vault"
    _write_entity(vault, "skills", {"title": "Skills"}, "# Skills\n\nFeature description.")
    found = find_vault_entity("skills", vault)
    assert found is not None
    assert found.name == "skills.md"


def test_find_vault_entity_by_human_name_slugifies(tmp_path: Path):
    vault = tmp_path / "vault"
    # Entity is stored as the slugified name "memory-tool".
    _write_entity(vault, "memory-tool", {"title": "Memory Tool"}, "body")
    found = find_vault_entity("Memory Tool", vault)
    assert found is not None
    assert found.name == "memory-tool.md"


def test_find_vault_entity_returns_none_for_missing(tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / "entities").mkdir(parents=True)
    assert find_vault_entity("nonexistent", vault) is None


def test_find_vault_entity_handles_missing_vault(tmp_path: Path):
    assert find_vault_entity("anything", tmp_path / "no_vault") is None


def test_read_entity_parses_frontmatter_and_body(tmp_path: Path):
    vault = tmp_path / "vault"
    path = _write_entity(
        vault,
        "skills",
        {
            "title": "Skills",
            "kind": "entity",
            "tags": ["claude-code", "skills"],
            "source_ids": ["ext_1234"],
            "endpoint_required": "anthropic_native",
            "business_relevance": "high",
            "briefing_status": "demo_worthy",
            "min_versions": {"claude_code": ">=2.1.116"},
        },
        "# Skills\n\nThe Skills feature lets agents register reusable capabilities.",
    )
    entity = read_entity(path)
    assert isinstance(entity, VaultEntity)
    assert entity.title == "Skills"
    assert entity.slug == "skills"
    assert entity.tags == ["claude-code", "skills"]
    assert entity.source_ids == ["ext_1234"]
    assert entity.endpoint_required == "anthropic_native"
    assert entity.business_relevance == "high"
    assert entity.briefing_status == "demo_worthy"
    assert "Skills feature" in entity.body


def test_read_entity_handles_pages_without_frontmatter(tmp_path: Path):
    path = tmp_path / "skills.md"
    path.write_text("# Skills\n\nNo frontmatter here.\n", encoding="utf-8")
    entity = read_entity(path)
    assert entity.title  # falls back to stem
    assert entity.frontmatter == {}
    assert "No frontmatter" in entity.body


def test_read_entity_raises_for_missing_path(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        read_entity(tmp_path / "missing.md")


# ── Source selection ────────────────────────────────────────────────────────


def test_select_relevant_sources_picks_by_source_id(tmp_path: Path):
    vault = tmp_path / "vault"
    sources = vault / "sources"
    sources.mkdir(parents=True)
    target = sources / "ext_1234567890_skills_doc.md"
    target.write_text("# Skills doc\n", encoding="utf-8")
    other = sources / "unrelated.md"
    other.write_text("# Unrelated\n", encoding="utf-8")
    path = _write_entity(vault, "skills", {"title": "Skills", "source_ids": ["ext_1234567890"]}, "body")
    entity = read_entity(path)
    picked = select_relevant_sources(entity, vault_root=vault, limit=10)
    assert target in picked
    assert other not in picked


def test_select_relevant_sources_picks_by_slug_match_in_raw(tmp_path: Path):
    vault = tmp_path / "vault"
    raw = vault / "raw" / "docs.anthropic.com"
    raw.mkdir(parents=True)
    matching = raw / "skills-overview.md"
    matching.write_text("# Skills overview\n", encoding="utf-8")
    other = raw / "memory-tool.md"
    other.write_text("# Memory tool\n", encoding="utf-8")
    path = _write_entity(vault, "skills", {"title": "Skills"}, "body")
    entity = read_entity(path)
    picked = select_relevant_sources(entity, vault_root=vault, limit=10)
    assert matching in picked


def test_select_relevant_sources_caps_at_limit(tmp_path: Path):
    vault = tmp_path / "vault"
    raw = vault / "raw"
    raw.mkdir(parents=True)
    for i in range(20):
        (raw / f"skills_{i}.md").write_text(f"# Skills {i}\n", encoding="utf-8")
    path = _write_entity(vault, "skills", {"title": "Skills"}, "body")
    entity = read_entity(path)
    picked = select_relevant_sources(entity, vault_root=vault, limit=5)
    assert len(picked) == 5


def test_select_relevant_sources_handles_missing_vault_dirs(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()  # vault exists but no raw/ or sources/
    path = _write_entity(vault, "skills", {"title": "Skills"}, "body")
    entity = read_entity(path)
    picked = select_relevant_sources(entity, vault_root=vault, limit=10)
    assert picked == []


# ── Template authoring ──────────────────────────────────────────────────────


def test_write_brief_template_includes_entity_context(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    vault = tmp_path / "vault"
    path = _write_entity(
        vault,
        "skills",
        {"title": "Skills", "summary": "Feature for registering reusable capabilities."},
        "# Skills\n\nLong-form body content about the Skills feature.",
    )
    entity = read_entity(path)
    sources_copied = [workspace / "SOURCES" / "skills_doc.md"]

    target = write_brief_template(workspace=workspace, entity=entity, sources_copied=sources_copied)
    text = target.read_text(encoding="utf-8")
    assert "Feature Briefing: Skills" in text
    assert "Feature for registering reusable capabilities." in text
    assert "Long-form body content" in text
    assert "skills_doc.md" in text
    # Placeholders for Simone are present.
    assert "(Simone:" in text


def test_write_acceptance_template_includes_endpoint_and_versions(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    vault = tmp_path / "vault"
    path = _write_entity(
        vault,
        "skills",
        {
            "title": "Skills",
            "endpoint_required": "anthropic_native",
            "min_versions": {"claude_code": ">=2.1.116"},
        },
        "body",
    )
    entity = read_entity(path)
    target = write_acceptance_template(workspace=workspace, entity=entity)
    text = target.read_text(encoding="utf-8")
    assert "Acceptance Contract: Skills" in text
    assert "endpoint_required=anthropic_native" in text
    assert "claude_code" in text and "2.1.116" in text


def test_write_business_relevance_template_carries_priority(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    vault = tmp_path / "vault"
    path = _write_entity(vault, "skills", {"title": "Skills", "business_relevance": "high"}, "body")
    entity = read_entity(path)
    target = write_business_relevance_template(workspace=workspace, entity=entity)
    text = target.read_text(encoding="utf-8")
    assert "Business Relevance: Skills" in text
    assert "`high`" in text


def test_write_business_relevance_default_priority_when_unknown(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    vault = tmp_path / "vault"
    path = _write_entity(vault, "skills", {"title": "Skills"}, "body")  # no business_relevance
    entity = read_entity(path)
    target = write_business_relevance_template(workspace=workspace, entity=entity)
    text = target.read_text(encoding="utf-8")
    assert "`medium`" in text


# ── Source copying ──────────────────────────────────────────────────────────


def test_populate_workspace_sources_copies_files(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    src = tmp_path / "doc1.md"
    src.write_text("# Doc 1\n", encoding="utf-8")
    written = populate_workspace_sources(workspace=workspace, sources=[src])
    assert len(written) == 1
    assert (workspace / "SOURCES" / "doc1.md").exists()
    assert (workspace / "SOURCES" / "doc1.md").read_text(encoding="utf-8") == "# Doc 1\n"


def test_populate_workspace_sources_skips_missing(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    src = tmp_path / "missing.md"  # never created
    written = populate_workspace_sources(workspace=workspace, sources=[src])
    assert written == []


def test_populate_workspace_sources_skips_directories(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    src_dir = tmp_path / "directory_not_file"
    src_dir.mkdir()
    written = populate_workspace_sources(workspace=workspace, sources=[src_dir])
    assert written == []


# ── Orchestration entry point ───────────────────────────────────────────────


def test_build_demo_scaffold_end_to_end(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos"))

    vault = tmp_path / "vault"
    raw = vault / "raw"
    raw.mkdir(parents=True)
    (raw / "skills_overview.md").write_text("# Skills Overview\n\nDocs body.\n", encoding="utf-8")

    entity_path = _write_entity(
        vault,
        "skills",
        {
            "title": "Skills",
            "summary": "Reusable capability registration",
            "endpoint_required": "anthropic_native",
            "business_relevance": "high",
            "tags": ["claude-code", "skills"],
        },
        "# Skills\n\nLong-form body.\n",
    )

    result = build_demo_scaffold(
        entity_path=entity_path,
        demo_id="skills__demo-1",
        vault_root=vault,
    )
    assert isinstance(result, ScaffoldArtifacts)
    assert result.workspace_dir.exists()
    assert result.workspace_dir.name == "skills__demo-1"
    assert result.brief_path.exists()
    assert result.acceptance_path.exists()
    assert result.business_relevance_path.exists()
    assert result.sources_dir.exists()
    # Source matched by filename slug → got copied.
    assert (result.sources_dir / "skills_overview.md").exists()
    assert any("Skills" in result.brief_path.read_text(encoding="utf-8") for _ in range(1))
    # Vanilla settings.json from PR 7 must be in place.
    assert (result.workspace_dir / ".claude" / "settings.json").exists()


def test_build_demo_scaffold_overwrite_replaces_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos"))
    vault = tmp_path / "vault"
    entity_path = _write_entity(vault, "skills", {"title": "Skills"}, "body")

    first = build_demo_scaffold(entity_path=entity_path, demo_id="skills__demo-1", vault_root=vault)
    (first.workspace_dir / "stale_marker.txt").write_text("stale", encoding="utf-8")
    assert (first.workspace_dir / "stale_marker.txt").exists()

    second = build_demo_scaffold(
        entity_path=entity_path,
        demo_id="skills__demo-1",
        vault_root=vault,
        overwrite=True,
    )
    # Stale marker must be gone after overwrite.
    assert not (second.workspace_dir / "stale_marker.txt").exists()
    assert second.brief_path.exists()


def test_build_demo_scaffold_refuses_to_clobber(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos"))
    vault = tmp_path / "vault"
    entity_path = _write_entity(vault, "skills", {"title": "Skills"}, "body")
    build_demo_scaffold(entity_path=entity_path, demo_id="skills__demo-1", vault_root=vault)
    with pytest.raises(FileExistsError):
        build_demo_scaffold(entity_path=entity_path, demo_id="skills__demo-1", vault_root=vault)
