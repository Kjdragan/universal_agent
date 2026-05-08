"""Tests for csi_intelligence_persistence — VaultDelta → vault file writes.

All tests use mocked ``VaultDelta`` objects (no LLM calls). The vault is
written to a temp directory, then the resulting file content is asserted.

Covers:
  - CREATE writes a new entity page with summary + key_facts + sources.
  - EXTEND appends a dated section to an existing page.
  - REVISE writes a snapshot to ``_history/`` then rewrites the page.
  - Auto-downgrade: CREATE on an existing page becomes EXTEND.
  - Auto-upgrade: EXTEND on a non-existent page becomes CREATE.
  - Light name canonicalization (``Memory (Claude Managed Agents)`` →
    ``Claude Managed Agents Memory``).
  - Relations append to ``relations.jsonl``.
  - Per-action errors are collected, not raised.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.services.csi_intelligence_pass import (
    VaultAction,
    VaultDelta,
    VaultRelation,
)
from universal_agent.services.csi_intelligence_persistence import (
    _canonicalize_name,
    apply_vault_delta_to_vault,
)
from universal_agent.wiki.core import ensure_vault, memex_page_exists

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Provision a fresh external vault under ``tmp_path``."""
    ctx = ensure_vault(
        "external",
        "csi-test",
        title="CSI test vault",
        root_override=str(tmp_path),
    )
    return ctx.path


def _read_page(vault_path: Path, kind_dir: str, slug: str) -> str:
    """Read the markdown file for a given kind/slug."""
    return (vault_path / kind_dir / f"{slug}.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Light name canonicalization
# ---------------------------------------------------------------------------


class TestCanonicalizeName:
    def test_memory_parenthetical_rewrite(self):
        assert _canonicalize_name("Memory (Claude Managed Agents)") == \
            "Claude Managed Agents Memory"

    def test_short_word_parenthetical_rewrite(self):
        assert _canonicalize_name("API (v2)") == "v2 API"
        assert _canonicalize_name("Beta (Skills)") == "Skills Beta"

    def test_long_prefix_not_rewritten(self):
        # Prefix is too long to be a tag-like word — preserve original
        assert _canonicalize_name("Mistral Mixture (the company)") == \
            "Mistral Mixture (the company)"

    def test_multiword_prefix_not_rewritten(self):
        # Multi-word prefix means real disambiguation — preserve
        assert _canonicalize_name("Claude Code (web)") == "Claude Code (web)"

    def test_no_parentheses_passthrough(self):
        assert _canonicalize_name("Claude Code") == "Claude Code"
        assert _canonicalize_name("MCP") == "MCP"

    def test_whitespace_stripped(self):
        assert _canonicalize_name("  Claude Code  ") == "Claude Code"


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------


class TestCreate:
    def test_create_writes_new_entity_page(self, vault: Path):
        delta = VaultDelta(
            vault_actions=[
                VaultAction(
                    op="create",
                    kind="product",
                    name="Claude Code",
                    aliases=["claude-code"],
                    summary="Anthropic's CLI for coding agents.",
                    key_facts=[
                        "Available on web and mobile",
                        "Supports MCP servers",
                    ],
                    source_post_ids=["2047371123185287223"],
                    source_doc_urls=["https://platform.claude.com/docs/code"],
                    confidence="high",
                )
            ],
        )
        result = apply_vault_delta_to_vault(
            delta,
            vault_path=vault,
            packet_id="packet-test-1",
            handle="ClaudeDevs",
        )

        assert result["counts"]["create"] == 1
        assert result["counts"]["extend"] == 0
        assert len(result["applied"]) == 1
        assert result["errors"] == []

        applied = result["applied"][0]
        assert applied["op"] == "CREATE"
        assert applied["name"] == "Claude Code"
        assert applied["llm_kind"] == "product"
        assert applied["memex_kind"] == "entity"
        assert applied["page_rel_path"] == "entities/claude-code.md"
        assert applied["log_note"] == ""

        page = _read_page(vault, "entities", "claude-code")
        # Frontmatter
        assert "title: Claude Code" in page
        assert "kind: entity" in page
        assert "provenance_kind: memex_create" in page
        assert "confidence: high" in page
        # Body
        assert "Anthropic's CLI for coding agents." in page
        assert "## Aliases" in page
        assert "`claude-code`" in page
        assert "## Key facts" in page
        assert "- Available on web and mobile" in page
        assert "- Supports MCP servers" in page
        assert "## Source posts" in page
        assert "[post `2047371123185287223`]" in page
        assert "https://x.com/ClaudeDevs/status/2047371123185287223" in page
        assert "## Source documents" in page
        assert "https://platform.claude.com/docs/code" in page
        assert "packet: `packet-test-1`" in page

        # log.md should have an entry
        log_md = (vault / "log.md").read_text(encoding="utf-8")
        assert "entities/claude-code.md CREATE" in log_md

    def test_create_with_minimal_fields(self, vault: Path):
        """No aliases, no key_facts, no source URLs — page still renders."""
        delta = VaultDelta(
            vault_actions=[
                VaultAction(
                    op="create",
                    kind="concept",
                    name="Prompt Caching",
                    summary="Pattern for reducing latency by caching prompts.",
                    confidence="medium",
                )
            ],
        )
        result = apply_vault_delta_to_vault(delta, vault_path=vault)
        assert result["counts"]["create"] == 1
        page = _read_page(vault, "concepts", "prompt-caching")
        assert "Pattern for reducing latency by caching prompts." in page
        assert "## Aliases" not in page
        assert "## Key facts" not in page
        assert "## Source posts" not in page

    def test_create_canonicalizes_memory_name(self, vault: Path):
        """The Memory (X) → X Memory rule applies before slugification."""
        delta = VaultDelta(
            vault_actions=[
                VaultAction(
                    op="create",
                    kind="feature",
                    name="Memory (Claude Managed Agents)",
                    summary="Per-agent persistent memory.",
                    confidence="medium",
                )
            ],
        )
        result = apply_vault_delta_to_vault(delta, vault_path=vault)
        assert result["counts"]["create"] == 1
        # The canonicalized name → "Claude Managed Agents Memory" → slug
        applied = result["applied"][0]
        assert applied["name"] == "Claude Managed Agents Memory"
        assert applied["page_rel_path"] == "entities/claude-managed-agents-memory.md"


# ---------------------------------------------------------------------------
# EXTEND
# ---------------------------------------------------------------------------


class TestExtend:
    def test_extend_appends_to_existing_page(self, vault: Path):
        # First, create the page
        create_delta = VaultDelta(
            vault_actions=[
                VaultAction(
                    op="create",
                    kind="product",
                    name="Claude Code",
                    summary="Anthropic's CLI for coding agents.",
                    confidence="high",
                )
            ],
        )
        apply_vault_delta_to_vault(create_delta, vault_path=vault)

        # Now extend it with new info
        extend_delta = VaultDelta(
            vault_actions=[
                VaultAction(
                    op="extend",
                    kind="product",
                    name="Claude Code",
                    existing_slug="claude-code",
                    summary="Now supports session recaps when switching focus.",
                    key_facts=["Session recap feature added 2026-05-08"],
                    source_post_ids=["2047371123185287777"],
                    confidence="high",
                )
            ],
        )
        result = apply_vault_delta_to_vault(
            extend_delta,
            vault_path=vault,
            packet_id="packet-test-2",
        )
        assert result["counts"]["extend"] == 1
        assert result["counts"]["create"] == 0

        page = _read_page(vault, "entities", "claude-code")
        # Original content preserved
        assert "Anthropic's CLI for coding agents." in page
        # New content appended
        assert "Now supports session recaps when switching focus." in page
        assert "Session recap feature added 2026-05-08" in page


# ---------------------------------------------------------------------------
# REVISE
# ---------------------------------------------------------------------------


class TestRevise:
    def test_revise_writes_history_snapshot_then_rewrites(self, vault: Path):
        # Create the page first
        create_delta = VaultDelta(
            vault_actions=[
                VaultAction(
                    op="create",
                    kind="product",
                    name="Old Product",
                    summary="Original description.",
                    confidence="medium",
                )
            ],
        )
        apply_vault_delta_to_vault(create_delta, vault_path=vault)
        # memex_page_exists takes the memex kind ("entity"/"concept"),
        # not the LLM-rich kind ("product"); product → entity.
        assert memex_page_exists(vault, "entity", "Old Product")

        # Now REVISE
        revise_delta = VaultDelta(
            vault_actions=[
                VaultAction(
                    op="revise",
                    kind="product",
                    name="Old Product",
                    existing_slug="old-product",
                    summary="Renamed and refocused — now describes the new direction.",
                    key_facts=["Deprecated in favor of New Thing"],
                    confidence="high",
                )
            ],
        )
        result = apply_vault_delta_to_vault(revise_delta, vault_path=vault)
        assert result["counts"]["revise"] == 1
        applied = result["applied"][0]
        assert applied["op"] == "REVISE"
        assert applied["snapshot_path"] is not None
        assert "_history" in applied["snapshot_path"]

        # The page now has the new content
        page = _read_page(vault, "entities", "old-product")
        assert "Renamed and refocused" in page
        assert "Deprecated in favor of New Thing" in page

        # The snapshot has the old content
        snapshot = Path(applied["snapshot_path"])
        assert snapshot.exists()
        snapshot_text = snapshot.read_text(encoding="utf-8")
        assert "Original description." in snapshot_text


# ---------------------------------------------------------------------------
# Auto-downgrade and auto-upgrade
# ---------------------------------------------------------------------------


class TestOpResolution:
    def test_create_on_existing_page_downgrades_to_extend(self, vault: Path):
        # Seed: page exists from a prior CREATE
        seed = VaultDelta(
            vault_actions=[
                VaultAction(
                    op="create",
                    kind="product",
                    name="Claude Code",
                    summary="First version.",
                    confidence="medium",
                )
            ],
        )
        apply_vault_delta_to_vault(seed, vault_path=vault)

        # The LLM unaware of vault state emits another CREATE for the same entity
        delta = VaultDelta(
            vault_actions=[
                VaultAction(
                    op="create",
                    kind="product",
                    name="Claude Code",
                    summary="Second packet's view.",
                    key_facts=["New fact from later packet"],
                    confidence="high",
                )
            ],
        )
        result = apply_vault_delta_to_vault(delta, vault_path=vault)

        # Should have been auto-downgraded to EXTEND
        assert result["counts"]["create"] == 0
        assert result["counts"]["extend"] == 1
        assert result["applied"][0]["op"] == "EXTEND"
        assert "downgrade CREATE" in result["applied"][0]["log_note"]

        # The page contains both old and new content
        page = _read_page(vault, "entities", "claude-code")
        assert "First version." in page
        assert "Second packet's view." in page

    def test_extend_on_missing_page_upgrades_to_create(self, vault: Path):
        # Vault is empty; LLM emits an EXTEND that targets a non-existent slug
        delta = VaultDelta(
            vault_actions=[
                VaultAction(
                    op="extend",
                    kind="product",
                    name="Imaginary Thing",
                    existing_slug="imaginary-thing",
                    summary="Brand new entity.",
                    confidence="medium",
                )
            ],
        )
        result = apply_vault_delta_to_vault(delta, vault_path=vault)
        assert result["counts"]["create"] == 1
        assert result["counts"]["extend"] == 0
        assert result["applied"][0]["op"] == "CREATE"
        assert "upgrade EXTEND" in result["applied"][0]["log_note"]

        page = _read_page(vault, "entities", "imaginary-thing")
        assert "Brand new entity." in page

    def test_revise_on_missing_page_upgrades_to_create(self, vault: Path):
        delta = VaultDelta(
            vault_actions=[
                VaultAction(
                    op="revise",
                    kind="product",
                    name="Ghost Entity",
                    existing_slug="ghost-entity",
                    summary="Should never have been a REVISE candidate.",
                    confidence="low",
                )
            ],
        )
        result = apply_vault_delta_to_vault(delta, vault_path=vault)
        assert result["counts"]["create"] == 1
        assert result["counts"]["revise"] == 0
        assert "upgrade REVISE" in result["applied"][0]["log_note"]


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------


class TestRelations:
    def test_relations_appended_to_jsonl(self, vault: Path):
        delta = VaultDelta(
            vault_actions=[],  # relations-only delta is valid
            relations=[
                VaultRelation(
                    from_slug="advisor-strategy",
                    to_slug="claude-managed-agents",
                    kind="feature-of",
                ),
                VaultRelation(
                    from_slug="advisor-strategy",
                    to_slug="opus-4-7",
                    kind="uses",
                ),
            ],
            post_summary="Beta announcement of advisor strategy.",
        )
        result = apply_vault_delta_to_vault(
            delta,
            vault_path=vault,
            packet_id="packet-test-rel",
        )
        assert result["relations_written"] == 2

        log_path = vault / "relations.jsonl"
        assert log_path.is_file()
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        rec0 = json.loads(lines[0])
        assert rec0["from_slug"] == "advisor-strategy"
        assert rec0["to_slug"] == "claude-managed-agents"
        assert rec0["kind"] == "feature-of"
        assert rec0["packet_id"] == "packet-test-rel"
        assert rec0["post_summary"] == "Beta announcement of advisor strategy."

    def test_relations_appended_idempotently_across_calls(self, vault: Path):
        delta = VaultDelta(
            relations=[
                VaultRelation(
                    from_slug="x", to_slug="y", kind="uses",
                )
            ],
        )
        apply_vault_delta_to_vault(delta, vault_path=vault, packet_id="p1")
        apply_vault_delta_to_vault(delta, vault_path=vault, packet_id="p2")
        log_lines = (vault / "relations.jsonl").read_text().strip().splitlines()
        # Both appends preserved (jsonl is append-only — dedup is a downstream concern)
        assert len(log_lines) == 2

    def test_no_relations_no_file_created(self, vault: Path):
        delta = VaultDelta(
            vault_actions=[
                VaultAction(
                    op="create",
                    kind="concept",
                    name="Standalone Concept",
                    summary="No relations.",
                    confidence="low",
                )
            ],
        )
        result = apply_vault_delta_to_vault(delta, vault_path=vault)
        assert result["relations_written"] == 0
        assert not (vault / "relations.jsonl").exists()


# ---------------------------------------------------------------------------
# Empty + error paths
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_delta_is_a_noop(self, vault: Path):
        delta = VaultDelta()  # no vault_actions, no relations
        result = apply_vault_delta_to_vault(delta, vault_path=vault)
        assert result["counts"] == {"create": 0, "extend": 0, "revise": 0}
        assert result["applied"] == []
        assert result["errors"] == []
        assert result["relations_written"] == 0

    def test_skipped_empty_name(self, vault: Path):
        delta = VaultDelta(
            vault_actions=[
                VaultAction(
                    op="create",
                    kind="product",
                    name="   ",  # only whitespace
                    summary="Should be skipped.",
                    confidence="low",
                )
            ],
        )
        result = apply_vault_delta_to_vault(delta, vault_path=vault)
        assert result["skipped_empty"] == 1
        assert result["counts"]["create"] == 0
        assert result["applied"] == []

    def test_per_action_errors_collected_not_raised(
        self, vault: Path, monkeypatch
    ):
        """If one VaultAction raises, others still apply and the error is recorded."""
        from universal_agent.services import csi_intelligence_persistence as mod

        original = mod.memex_apply_action
        call_count = {"n": 0}

        def flaky(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated transient failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(mod, "memex_apply_action", flaky)

        delta = VaultDelta(
            vault_actions=[
                VaultAction(
                    op="create", kind="product", name="Will Fail",
                    summary="The first action raises.", confidence="medium",
                ),
                VaultAction(
                    op="create", kind="product", name="Will Succeed",
                    summary="The second action persists.", confidence="medium",
                ),
            ],
        )
        result = apply_vault_delta_to_vault(delta, vault_path=vault)
        assert len(result["errors"]) == 1
        assert result["errors"][0]["name"] == "Will Fail"
        assert "simulated transient failure" in result["errors"][0]["error"]
        assert len(result["applied"]) == 1
        assert result["applied"][0]["name"] == "Will Succeed"


# ---------------------------------------------------------------------------
# Tags + frontmatter
# ---------------------------------------------------------------------------


class TestTagsAndProvenance:
    def test_tags_include_kind_source_confidence_packet(self, vault: Path):
        delta = VaultDelta(
            vault_actions=[
                VaultAction(
                    op="create",
                    kind="feature",
                    name="Outcomes Loop",
                    summary="Rubric-driven self-improvement.",
                    confidence="high",
                )
            ],
        )
        apply_vault_delta_to_vault(delta, vault_path=vault, packet_id="2026-05-06/210011")

        page = _read_page(vault, "entities", "outcomes-loop")
        # Tags are emitted as a YAML list inside frontmatter
        assert "kind:feature" in page
        assert "source:csi-claude-code" in page
        assert "confidence:high" in page
        assert "packet:2026-05-06/210011" in page
