"""Tests for the Memex update primitives (PR 2).

Verifies CREATE/EXTEND/REVISE behavior including:
- CREATE writes a new page; rejects duplicates.
- EXTEND appends a dated section without modifying prior content.
- REVISE snapshots to _history/ before overwriting.
- memex_apply_action dispatches and appends a structured change-log entry.
- Frontmatter source_ids/provenance_refs accumulate across operations.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.wiki.core import (
    ACTION_CREATE,
    ACTION_EXTEND,
    ACTION_REVISE,
    MEMEX_ACTIONS,
    _frontmatter_and_body,
    ensure_vault,
    memex_append_change_log,
    memex_apply_action,
    memex_create_page,
    memex_extend_page,
    memex_page_exists,
    memex_revise_page,
    memex_snapshot_to_history,
)


@pytest.fixture
def vault(tmp_path: Path):
    ctx = ensure_vault(
        "external",
        "test-memex-vault",
        title="Test Memex Vault",
        root_override=str(tmp_path / "vault"),
    )
    return ctx.path


def test_create_writes_new_entity_page(vault: Path):
    assert memex_page_exists(vault, "entity", "Skills") is False
    page_path = memex_create_page(
        vault,
        "entity",
        "Skills",
        "# Skills\n\nFeature for registering reusable agent capabilities.\n",
        source_id="ext_2026050300",
        source_title="Anthropic Skills launch",
        tags=["claude-code", "skills"],
    )
    assert page_path.exists()
    assert page_path.name == "skills.md"
    assert page_path.parent.name == "entities"

    meta, body = _frontmatter_and_body(page_path)
    assert meta["title"] == "Skills"
    assert meta["kind"] == "entity"
    assert "ext_2026050300" in meta["source_ids"]
    assert "ext_2026050300" in meta["provenance_refs"]
    assert "claude-code" in meta["tags"]
    assert "Feature for registering" in body


def test_create_refuses_to_overwrite_existing(vault: Path):
    memex_create_page(vault, "entity", "Hooks", "first body")
    with pytest.raises(FileExistsError):
        memex_create_page(vault, "entity", "Hooks", "different body")


def test_extend_appends_dated_section(vault: Path):
    memex_create_page(
        vault,
        "entity",
        "Memory_Tool",
        "# Memory Tool\n\nInitial description.",
        source_id="ext_initial",
    )
    page_path = memex_extend_page(
        vault,
        "entity",
        "Memory_Tool",
        "Beta gained support for typed schemas this week.",
        source_id="ext_followup",
        source_title="Memory Tool typed schemas",
    )
    meta, body = _frontmatter_and_body(page_path)
    assert "Initial description." in body
    assert "Beta gained support for typed schemas" in body
    assert "ext_initial" in meta["source_ids"]
    assert "ext_followup" in meta["source_ids"]


def test_extend_refuses_when_page_missing(vault: Path):
    with pytest.raises(FileNotFoundError):
        memex_extend_page(vault, "entity", "DoesNotExist", "addition")


def test_revise_snapshots_then_overwrites(vault: Path):
    page_path = memex_create_page(
        vault,
        "entity",
        "Subagents",
        "# Subagents\n\nOriginal claim: subagents are isolated processes.\n",
        source_id="ext_old",
    )
    revised_path, snapshot_path = memex_revise_page(
        vault,
        "entity",
        "Subagents",
        "# Subagents\n\nUpdated claim: subagents share context window with parent.\n",
        source_id="ext_new",
        source_title="Subagents context-sharing release",
        reason="Original claim about isolation was superseded by 2026-05-01 release",
    )
    assert revised_path == page_path
    assert snapshot_path is not None
    assert snapshot_path.exists()

    snapshot_text = snapshot_path.read_text(encoding="utf-8")
    assert "Original claim: subagents are isolated processes." in snapshot_text

    current_text = page_path.read_text(encoding="utf-8")
    assert "share context window" in current_text
    assert "Original claim: subagents are isolated processes." not in current_text

    meta, _ = _frontmatter_and_body(page_path)
    assert "ext_new" in meta["source_ids"]
    assert "ext_old" in meta["source_ids"]
    assert meta["last_revision_reason"].startswith("Original claim about isolation")


def test_revise_requires_reason(vault: Path):
    memex_create_page(vault, "entity", "Hooks", "# Hooks\n")
    with pytest.raises(ValueError):
        memex_revise_page(
            vault,
            "entity",
            "Hooks",
            "rewritten body",
            reason="",  # empty reason — must be rejected for audit integrity
        )


def test_snapshot_returns_none_when_page_missing(vault: Path):
    assert memex_snapshot_to_history(vault, "entity", "NeverCreated") is None


def test_change_log_records_structured_entry(vault: Path):
    memex_append_change_log(
        vault,
        action=ACTION_CREATE,
        page_rel_path="entities/skills.md",
        reason="Newly announced feature",
        source_ids=["ext_2026050300"],
        confidence="high",
    )
    log_text = (vault / "log.md").read_text(encoding="utf-8")
    assert "entities/skills.md CREATE" in log_text
    assert "reason: Newly announced feature" in log_text
    assert "confidence: high" in log_text
    assert "ext_2026050300" in log_text


def test_change_log_rejects_unknown_action(vault: Path):
    with pytest.raises(ValueError):
        memex_append_change_log(
            vault,
            action="DESTROY",
            page_rel_path="entities/x.md",
            reason="",
        )


def test_apply_action_create_writes_page_and_log(vault: Path):
    result = memex_apply_action(
        vault,
        action=ACTION_CREATE,
        kind="entity",
        name="Plugins",
        body="# Plugins\n\nMarketplace integration for skills.\n",
        source_id="ext_2026050314",
        source_title="Plugins marketplace launch",
        tags=["plugins", "marketplace"],
    )
    assert result["action"] == ACTION_CREATE
    assert result["page_rel_path"] == "entities/plugins.md"
    assert result["snapshot_path"] is None
    assert (vault / "entities" / "plugins.md").exists()
    log_text = (vault / "log.md").read_text(encoding="utf-8")
    assert "entities/plugins.md CREATE" in log_text


def test_apply_action_revise_creates_snapshot(vault: Path):
    memex_create_page(vault, "concept", "Caching", "# Caching\n\nv1.\n")
    result = memex_apply_action(
        vault,
        action=ACTION_REVISE,
        kind="concept",
        name="Caching",
        body="# Caching\n\nv2 — corrected behavior under concurrent writes.\n",
        source_id="ext_2026051001",
        reason="Concurrent write semantics changed in v0.6 release",
    )
    assert result["snapshot_path"] is not None
    assert Path(result["snapshot_path"]).exists()
    assert "concepts/caching.md REVISE" in (vault / "log.md").read_text(encoding="utf-8")


def test_apply_action_extend_appends_section(vault: Path):
    memex_create_page(vault, "entity", "Skills", "# Skills\n\nInitial.\n")
    result = memex_apply_action(
        vault,
        action=ACTION_EXTEND,
        kind="entity",
        name="Skills",
        body="Skills now support cross-project references.",
        source_id="ext_2026051501",
        source_title="Skills cross-project refs",
    )
    assert result["action"] == ACTION_EXTEND
    page_text = (vault / "entities" / "skills.md").read_text(encoding="utf-8")
    assert "Initial." in page_text
    assert "Skills now support cross-project references." in page_text
    assert "entities/skills.md EXTEND" in (vault / "log.md").read_text(encoding="utf-8")


def test_apply_action_rejects_unknown_action(vault: Path):
    with pytest.raises(ValueError):
        memex_apply_action(
            vault,
            action="MERGE",
            kind="entity",
            name="X",
            body="...",
        )


def test_kind_validation(vault: Path):
    with pytest.raises(ValueError):
        memex_create_page(vault, "decision", "X", "body")


def test_actions_constant_exposes_all_three():
    assert set(MEMEX_ACTIONS) == {ACTION_CREATE, ACTION_EXTEND, ACTION_REVISE}


def test_history_dir_separates_entities_and_concepts(vault: Path):
    memex_create_page(vault, "entity", "Skills", "# Skills\n")
    memex_create_page(vault, "concept", "Skills", "# Skills concept\n")
    snap1 = memex_snapshot_to_history(vault, "entity", "Skills")
    snap2 = memex_snapshot_to_history(vault, "concept", "Skills")
    assert snap1 is not None and snap2 is not None
    # Snapshots must land in separate per-kind subtrees so name collisions
    # between an entity and a concept of the same name don't blow up history.
    assert "entities" in str(snap1) and "concepts" in str(snap2)
    assert snap1 != snap2
