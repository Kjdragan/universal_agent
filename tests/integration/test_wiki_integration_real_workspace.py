"""Integration tests for the LLM Wiki system against the real workspace.

These tests validate the wiki engine against:
1. The real shared-memory workspace (internal sync)
2. A real document from the repo (external ingest)

Tests are skipped when the workspace is not available (e.g., in CI).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from universal_agent.wiki import core


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------

def _shared_memory_available() -> bool:
    """Check if the real shared-memory workspace exists."""
    try:
        from universal_agent.memory.paths import resolve_shared_memory_workspace
        root = Path(resolve_shared_memory_workspace())
        return root.exists() and (root / "MEMORY.md").exists()
    except Exception:
        return False


def _repo_doc_available() -> bool:
    """Check if the LLM Wiki System doc exists in the repo."""
    doc = Path(__file__).resolve().parents[2] / "docs" / "02_Subsystems" / "LLM_Wiki_System.md"
    return doc.exists()


skip_no_shared_memory = pytest.mark.skipif(
    not _shared_memory_available(),
    reason="Real shared-memory workspace not available",
)

skip_no_repo_doc = pytest.mark.skipif(
    not _repo_doc_available(),
    reason="LLM Wiki System doc not found in repo",
)


# ---------------------------------------------------------------------------
# Internal sync against real shared memory
# ---------------------------------------------------------------------------

@skip_no_shared_memory
class TestInternalSyncRealWorkspace:
    """Validate internal wiki sync against the actual Memory_System workspace."""

    def test_internal_sync_completes_within_timeout(self):
        """Sync should complete within 30s against real data."""
        result = core.sync_internal_memory_vault(trigger="integration_test")

        assert result["status"] == "success"
        assert result["total_duration_ms"] < 30_000, (
            f"Sync took {result['total_duration_ms']}ms, exceeding 30s budget"
        )

    def test_decision_ledger_exists_and_has_content(self):
        """The decision ledger should be materialized with substantive content."""
        result = core.sync_internal_memory_vault(trigger="integration_test_ledger")

        vault_path = Path(result["vault_path"])
        ledger = vault_path / "decisions" / "decision-ledger.md"
        assert ledger.exists(), "Decision ledger not materialized"

        content = ledger.read_text(encoding="utf-8")
        # Should have more than just a heading
        body_lines = [
            line for line in content.splitlines()
            if line.strip() and not line.startswith("---") and not line.startswith("#")
        ]
        assert len(body_lines) >= 2, f"Decision ledger too thin: {len(body_lines)} body lines"

    def test_sync_state_is_coherent(self):
        """sync_state.json should be valid JSON with expected keys."""
        result = core.sync_internal_memory_vault(trigger="integration_test_state")

        vault_path = Path(result["vault_path"])
        state_path = vault_path / "sync_state.json"
        assert state_path.exists(), "sync_state.json not found"

        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert isinstance(state, dict)
        assert "source_fingerprints" in state
        assert isinstance(state["source_fingerprints"], dict)

    def test_sync_progress_is_human_readable(self):
        """sync_progress.md should exist and be readable."""
        result = core.sync_internal_memory_vault(trigger="integration_test_progress")

        vault_path = Path(result["vault_path"])
        progress_md = vault_path / "sync_progress.md"
        assert progress_md.exists(), "sync_progress.md not found"

        content = progress_md.read_text(encoding="utf-8")
        assert "# Sync Progress" in content
        assert "Status:" in content

    def test_warm_rerun_skips_unchanged_files(self):
        """A second sync should skip previously copied files."""
        # First run
        core.sync_internal_memory_vault(trigger="integration_test_warm_1")

        # Second run
        result = core.sync_internal_memory_vault(trigger="integration_test_warm_2")

        skipped = result.get("skipped_counts", {})
        total_skipped = sum(skipped.values()) if isinstance(skipped, dict) else 0
        assert total_skipped > 0, f"Expected skips on warm rerun, got: {skipped}"


# ---------------------------------------------------------------------------
# External ingest with real document
# ---------------------------------------------------------------------------

@skip_no_repo_doc
class TestExternalIngestRealDocument:
    """Validate external ingest against a real document from the repo."""

    @pytest.fixture
    def vault_root(self, tmp_path):
        return str(tmp_path / "integration_vaults")

    @pytest.fixture
    def real_doc_path(self):
        return str(
            Path(__file__).resolve().parents[2]
            / "docs" / "02_Subsystems" / "LLM_Wiki_System.md"
        )

    def test_ingest_real_document_succeeds(self, vault_root, real_doc_path):
        """Ingesting a real document should succeed without errors."""
        core.ensure_vault("external", "integration-test", root_override=vault_root)
        result = core.ingest_external_source(
            vault_slug="integration-test",
            source_path=real_doc_path,
            title="LLM Wiki System Documentation",
            root_override=vault_root,
        )

        assert result["status"] == "success"
        assert result["source_page"].startswith("sources/")

    def test_query_returns_relevant_results(self, vault_root, real_doc_path):
        """Querying the vault should return relevant matches."""
        core.ensure_vault("external", "query-integration", root_override=vault_root)
        core.ingest_external_source(
            vault_slug="query-integration",
            source_path=real_doc_path,
            title="LLM Wiki System Documentation",
            root_override=vault_root,
        )

        query_result = core.query_vault(
            vault_kind="external",
            vault_slug="query-integration",
            query="external vault structure and source ingestion",
            root_override=vault_root,
        )

        assert query_result["matches"], "Query should return at least one match"

    def test_lint_produces_no_critical_findings(self, vault_root, real_doc_path):
        """After ingest, lint should produce no broken wikilinks."""
        core.ensure_vault("external", "lint-integration", root_override=vault_root)
        core.ingest_external_source(
            vault_slug="lint-integration",
            source_path=real_doc_path,
            title="LLM Wiki System Documentation",
            root_override=vault_root,
        )

        lint_result = core.lint_vault(
            vault_kind="external",
            vault_slug="lint-integration",
            root_override=vault_root,
        )

        # No broken wikilinks should exist
        broken = [f for f in lint_result["findings"] if f["kind"] == "broken_wikilink"]
        assert len(broken) == 0, f"Found broken wikilinks: {broken}"

    def test_vault_index_is_populated(self, vault_root, real_doc_path):
        """The index.md should contain entries after ingest."""
        core.ensure_vault("external", "index-integration", root_override=vault_root)
        core.ingest_external_source(
            vault_slug="index-integration",
            source_path=real_doc_path,
            title="LLM Wiki System Documentation",
            root_override=vault_root,
        )

        vault_path = Path(vault_root) / "index-integration"
        index_text = (vault_path / "index.md").read_text(encoding="utf-8")
        assert "[[sources/" in index_text, "Index should contain source page entry"
