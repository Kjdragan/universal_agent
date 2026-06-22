"""Tests for the paper_to_podcast cache-path fix and fail-loud guard.

Covers two fixes for the 2026-06-22 silent no-op:

1. Cache-path alignment: ``arxiv_runtime.canonical_arxiv_storage_path`` /
   ``resolve_cached_paper_path`` / ``is_paper_cached`` resolve to the ONE path
   the arxiv-mcp-server writes to. The server writes every paper (HTML or PDF
   source) as ``<paper_id>.md`` - these tests pin that contract.

2. Fail-loud guard: ``paper_to_podcast_guard.evaluate_paper_to_podcast_run``
   inspects a run's work products and returns ``is_failure=True`` when zero
   usable papers are evidenced - so the cron wrapper can flip rc=0 to rc=1
   and the silent-no-op class cannot recur.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.arxiv_runtime import (
    canonical_arxiv_storage_path,
    is_paper_cached,
    resolve_cached_paper_path,
)
from universal_agent.services.paper_to_podcast_guard import (
    PaperToPodcastRunResult,
    evaluate_paper_to_podcast_run,
)

# ---------------------------------------------------------------------------
# Cache-path resolution
# ---------------------------------------------------------------------------


class TestCanonicalStoragePath:
    """canonical_arxiv_storage_path resolves one deterministic path."""

    def test_honours_env_override(self, monkeypatch, tmp_path):
        expected = tmp_path / "custom_arxiv_cache"
        monkeypatch.setenv("UA_ARXIV_MCP_STORAGE_PATH", str(expected))
        result = canonical_arxiv_storage_path()
        assert result == expected.resolve()
        # Directory is created on resolution so the server never falls back.
        assert result.is_dir()

    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("UA_ARXIV_MCP_STORAGE_PATH", raising=False)
        result = canonical_arxiv_storage_path()
        # Default is the server's historical home-default (so the existing
        # papers remain reachable without a migration).
        assert result == (Path.home() / ".arxiv-mcp-server" / "papers").resolve()

    def test_empty_env_falls_through_to_default(self, monkeypatch):
        monkeypatch.setenv("UA_ARXIV_MCP_STORAGE_PATH", "   ")
        result = canonical_arxiv_storage_path()
        assert result == (Path.home() / ".arxiv-mcp-server" / "papers").resolve()


class TestResolveCachedPaperPath:
    """Papers are stored as <id>.md regardless of HTML vs PDF source."""

    def test_md_suffix_not_pdf(self, monkeypatch, tmp_path):
        """The server stores every paper as .md; .pdf MUST NOT be produced.

        This pins the root-cause fix: the 2026-06-22 no-op was caused in part
        by the pipeline looking for a .pdf that the server never produces.
        """
        monkeypatch.setenv("UA_ARXIV_MCP_STORAGE_PATH", str(tmp_path))
        path = resolve_cached_paper_path("2512.11668")
        assert path.suffix == ".md"
        assert path.name == "2512.11668.md"
        assert ".pdf" not in path.name

    def test_html_source_paper_resolves_same_as_pdf(self, monkeypatch, tmp_path):
        """HTML-source and PDF-source papers share the same path shape."""
        monkeypatch.setenv("UA_ARXIV_MCP_STORAGE_PATH", str(tmp_path))
        # The failing 2026-06-22 paper was source: html.
        html_paper = resolve_cached_paper_path("2512.11668")
        # A PDF-source paper (hypothetical) resolves to the same suffix.
        pdf_paper = resolve_cached_paper_path("2606.06032")
        assert html_paper.suffix == pdf_paper.suffix == ".md"
        assert html_paper.parent == pdf_paper.parent

    def test_arxiv_prefix_stripped(self, monkeypatch, tmp_path):
        monkeypatch.setenv("UA_ARXIV_MCP_STORAGE_PATH", str(tmp_path))
        assert resolve_cached_paper_path("arXiv:2512.11668").name == "2512.11668.md"

    def test_path_separators_collapsed(self, monkeypatch, tmp_path):
        """An attacker-controlled id cannot escape the storage directory."""
        monkeypatch.setenv("UA_ARXIV_MCP_STORAGE_PATH", str(tmp_path))
        sneaky = resolve_cached_paper_path("../etc/passwd")
        # The separators are collapsed so the resolved path stays inside the
        # cache directory (the parent is the storage root, not /etc or worse).
        assert sneaky.parent == tmp_path.resolve()
        assert sneaky.is_absolute()
        # The resolved path must be a descendant of the storage root.
        assert str(sneaky).startswith(str(tmp_path.resolve()))


class TestIsPaperCached:
    """is_paper_cached handles both .md (HTML) and .md (PDF) source papers."""

    def test_cached_html_paper_found(self, monkeypatch, tmp_path):
        """The exact 2026-06-22 failing paper (source: html) is found.

        Before the fix the pipeline's cache check missed this paper. After the
        fix is_paper_cached resolves to the .md path the server actually wrote.
        """
        monkeypatch.setenv("UA_ARXIV_MCP_STORAGE_PATH", str(tmp_path))
        # Simulate the server having written the HTML-source paper as .md.
        (tmp_path / "2512.11668.md").write_text("paper text", encoding="utf-8")
        assert is_paper_cached("2512.11668") is True

    def test_cached_pdf_source_paper_found(self, monkeypatch, tmp_path):
        """A PDF-source paper (also stored as .md by the server) is found."""
        monkeypatch.setenv("UA_ARXIV_MCP_STORAGE_PATH", str(tmp_path))
        (tmp_path / "2606.06032.md").write_text("converted markdown", encoding="utf-8")
        assert is_paper_cached("2606.06032") is True

    def test_uncached_paper_not_found(self, monkeypatch, tmp_path):
        monkeypatch.setenv("UA_ARXIV_MCP_STORAGE_PATH", str(tmp_path))
        assert is_paper_cached("9999.99999") is False

    def test_pdf_file_is_not_treated_as_cache_hit(self, monkeypatch, tmp_path):
        """A stray .pdf in the cache dir MUST NOT satisfy is_paper_cached.

        The server deletes the intermediate PDF after conversion; a .pdf being
        present is not a valid cache signal for the pipeline.
        """
        monkeypatch.setenv("UA_ARXIV_MCP_STORAGE_PATH", str(tmp_path))
        (tmp_path / "2512.11668.pdf").write_text("not a real pdf", encoding="utf-8")
        assert is_paper_cached("2512.11668") is False


# ---------------------------------------------------------------------------
# Fail-loud guard
# ---------------------------------------------------------------------------


class TestEvaluatePaperToPodcastRun:
    """evaluate_paper_to_podcast_run flips zero-paper runs to failure."""

    def test_manifest_with_papers_is_success(self, tmp_path):
        wp = tmp_path / "work_products" / "paper_to_podcast"
        wp.mkdir(parents=True)
        (wp / "manifest.json").write_text(
            json.dumps({"papers": [{"id": "2512.11668"}, {"id": "2606.06032"}]}),
            encoding="utf-8",
        )
        result = evaluate_paper_to_podcast_run(tmp_path)
        assert result.is_failure is False
        assert result.usable_paper_count == 2
        assert "manifest.json" in result.reason

    def test_papers_metadata_with_papers_is_success(self, tmp_path):
        """Fallback evidence: papers_metadata.json with >=1 paper."""
        wp = tmp_path / "work_products" / "paper_to_podcast"
        wp.mkdir(parents=True)
        (wp / "papers_metadata.json").write_text(
            json.dumps([{"id": "2512.11668"}]),
            encoding="utf-8",
        )
        result = evaluate_paper_to_podcast_run(tmp_path)
        assert result.is_failure is False
        assert result.usable_paper_count == 1

    def test_empty_manifest_is_failure(self, tmp_path):
        """The 2026-06-22 signature: manifest exists but lists 0 papers."""
        wp = tmp_path / "work_products" / "paper_to_podcast"
        wp.mkdir(parents=True)
        (wp / "manifest.json").write_text(
            json.dumps({"papers": []}),
            encoding="utf-8",
        )
        result = evaluate_paper_to_podcast_run(tmp_path)
        assert result.is_failure is True
        assert result.usable_paper_count == 0
        assert "ZERO usable papers" in result.reason or "0 papers" in result.reason

    def test_no_artifacts_is_failure(self, tmp_path):
        """No manifest AND no papers_metadata -> silent-no-op -> failure."""
        # work_products dir does not even exist.
        result = evaluate_paper_to_podcast_run(tmp_path)
        assert result.is_failure is True
        assert result.usable_paper_count == 0

    def test_failure_sentinel_honoured(self, tmp_path):
        """The skill Phase A step 5 writes FAILURE.txt on zero downloads.

        The guard MUST honour this explicit failure signal even if other
        artifacts are present.
        """
        wp = tmp_path / "work_products" / "paper_to_podcast"
        wp.mkdir(parents=True)
        (wp / "FAILURE.txt").write_text(
            "All 5 download_paper calls returned status: error",
            encoding="utf-8",
        )
        result = evaluate_paper_to_podcast_run(tmp_path)
        assert result.is_failure is True
        assert "FAILURE.txt sentinel" in result.reason

    def test_manifest_one_paper_exactly_is_success(self, tmp_path):
        """The minimum bar: exactly 1 usable paper is a clean run."""
        wp = tmp_path / "work_products" / "paper_to_podcast"
        wp.mkdir(parents=True)
        (wp / "manifest.json").write_text(
            json.dumps({"papers": [{"id": "2512.11668"}]}),
            encoding="utf-8",
        )
        result = evaluate_paper_to_podcast_run(tmp_path)
        assert result.is_failure is False
        assert result.usable_paper_count == 1

    def test_corrupt_manifest_does_not_crash(self, tmp_path):
        """A corrupt manifest.json is treated as missing evidence, not a crash."""
        wp = tmp_path / "work_products" / "paper_to_podcast"
        wp.mkdir(parents=True)
        (wp / "manifest.json").write_text("{not valid json", encoding="utf-8")
        # No papers_metadata either -> failure (not an exception).
        result = evaluate_paper_to_podcast_run(tmp_path)
        assert result.is_failure is True

    def test_papers_metadata_empty_list_is_failure(self, tmp_path):
        wp = tmp_path / "work_products" / "paper_to_podcast"
        wp.mkdir(parents=True)
        (wp / "papers_metadata.json").write_text("[]", encoding="utf-8")
        result = evaluate_paper_to_podcast_run(tmp_path)
        assert result.is_failure is True
        assert result.usable_paper_count == 0

    def test_run_result_is_frozen(self):
        """PaperToPodcastRunResult is a frozen dataclass (immutable)."""
        r = PaperToPodcastRunResult(is_failure=True, reason="x", usable_paper_count=0)
        with pytest.raises(Exception):
            r.is_failure = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Integration: the cache check that MISSED 2512.11668 now finds it
# ---------------------------------------------------------------------------


class TestFailingPaperResolved:
    """Direct regression test for the 2026-06-22 failing paper id."""

    def test_2512_11668_resolves_as_cached_when_on_disk(self, monkeypatch, tmp_path):
        """Reproduces the exact failing paper from the 2026-06-22 run.

        Before the fix: the pipeline's cache check could not find this paper
        (it was looking for a .pdf at the wrong path). After the fix:
        is_paper_cached resolves to <storage>/2512.11668.md - the path the
        arxiv-mcp-server's download_paper actually wrote (source: html).
        """
        monkeypatch.setenv("UA_ARXIV_MCP_STORAGE_PATH", str(tmp_path))
        # Simulate the server's HTML-source write (download_paper returned
        # {"status": "success", "source": "html"} and wrote 2512.11668.md).
        cached_path = tmp_path / "2512.11668.md"
        cached_path.write_text(
            "Bridging Streaming Continual Learning via In-Context Large Tabular Models",
            encoding="utf-8",
        )
        # BEFORE-fix behaviour (looking for a .pdf) would miss this. AFTER-fix:
        assert is_paper_cached("2512.11668") is True
        assert resolve_cached_paper_path("2512.11668") == cached_path.resolve()
