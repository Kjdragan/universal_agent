"""
Unit tests for pure helper functions in src/universal_agent/cli_io.py

Covers:
- summarize_response: whitespace collapse, truncation, edge cases
- _normalize_display_path: session path rewriting for display
- list_workspace_artifacts: artifact file listing
- collect_local_tool_trace_ids: trace ID extraction from run logs
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from universal_agent.cli_io import (
    _normalize_display_path,
    collect_local_tool_trace_ids,
    list_workspace_artifacts,
    summarize_response,
)

# -- summarize_response ------------------------------------------------


class TestSummarizeResponse:
    def test_empty_string(self):
        assert summarize_response("") == ""


    def test_short_text_unchanged(self):
        text = "Hello world"
        assert summarize_response(text) == text

    def test_whitespace_collapsed(self):
        text = "line one\n\n   line two\t\tline three"
        result = summarize_response(text)
        assert result == "line one line two line three"

    def test_truncation_at_max_chars(self):
        text = "x" * 1000
        result = summarize_response(text, max_chars=100)
        assert len(result) == 100
        assert result.endswith("...")

    def test_exact_max_chars_unchanged(self):
        text = "a" * 50
        result = summarize_response(text, max_chars=50)
        assert result == text
        assert not result.endswith("...")

    def test_custom_max_chars(self):
        text = "a" * 200
        result = summarize_response(text, max_chars=50)
        assert len(result) == 50
        assert result.endswith("...")


# -- _normalize_display_path -------------------------------------------


class TestNormalizeDisplayPath:
    def test_non_string_passthrough(self):
        assert _normalize_display_path(42, "/ws") == 42

    def test_empty_string_passthrough(self):
        assert _normalize_display_path("", "/ws") == ""

    def test_none_workspace_passthrough(self):
        assert _normalize_display_path("/some/path", None) == "/some/path"

    def test_non_session_path_passthrough(self):
        path = "/opt/universal_agent/some_file.txt"
        assert _normalize_display_path(path, "/ws") == path

    def test_work_products_marker(self):
        path = "/home/user/.claude/sessions/abc/work_products/report.html"
        result = _normalize_display_path(path, "/workspace")
        assert result == os.path.join("/workspace", "work_products", "report.html")

    def test_search_results_marker(self):
        path = "/home/user/.claude/sessions/abc/search_results/data.json"
        result = _normalize_display_path(path, "/workspace")
        assert result == os.path.join("/workspace", "search_results", "data.json")

    def test_downloads_marker(self):
        path = "/home/user/.claude/sessions/abc/downloads/file.pdf"
        result = _normalize_display_path(path, "/workspace")
        assert result == os.path.join("/workspace", "downloads", "file.pdf")

    def test_search_results_filtered_best_marker(self):
        path = "/home/user/.claude/sessions/abc/search_results_filtered_best/article.md"
        result = _normalize_display_path(path, "/workspace")
        assert result == os.path.join(
            "/workspace", "search_results_filtered_best", "article.md"
        )

    def test_workbench_marker(self):
        path = "/home/user/.claude/sessions/abc/workbench/output.csv"
        result = _normalize_display_path(path, "/workspace")
        assert result == os.path.join("/workspace", "workbench", "output.csv")

    def test_no_marker_falls_back_to_work_products_basename(self):
        path = "/home/user/.claude/sessions/abc/mystery_dir/file.txt"
        result = _normalize_display_path(path, "/workspace")
        assert result == os.path.join("/workspace", "work_products", "file.txt")


# -- list_workspace_artifacts ------------------------------------------


class TestListWorkspaceArtifacts:
    def test_empty_dir(self, tmp_path: Path):
        assert list_workspace_artifacts(str(tmp_path)) == []

    def test_finds_html_pdf_pptx(self, tmp_path: Path):
        (tmp_path / "report.html").write_text("<h1>hi</h1>")
        (tmp_path / "slides.pdf").write_bytes(b"%PDF")
        (tmp_path / "deck.pptx").write_bytes(b"PK")
        result = list_workspace_artifacts(str(tmp_path))
        assert sorted(result) == ["deck.pptx", "report.html", "slides.pdf"]

    def test_ignores_other_extensions(self, tmp_path: Path):
        (tmp_path / "data.json").write_text("{}")
        (tmp_path / "image.png").write_bytes(b"\x89PNG")
        assert list_workspace_artifacts(str(tmp_path)) == []

    def test_nonexistent_dir(self):
        assert list_workspace_artifacts("/no/such/dir") == []

    def test_empty_string(self):
        assert list_workspace_artifacts("") == []

    def test_returns_sorted(self, tmp_path: Path):
        (tmp_path / "z.html").write_text("z")
        (tmp_path / "a.html").write_text("a")
        result = list_workspace_artifacts(str(tmp_path))
        assert result == ["a.html", "z.html"]


# -- collect_local_tool_trace_ids --------------------------------------


class TestCollectLocalToolTraceIds:
    def test_empty_dir(self, tmp_path: Path):
        assert collect_local_tool_trace_ids(str(tmp_path)) == []

    def test_empty_string(self):
        assert collect_local_tool_trace_ids("") == []

    def test_no_log_file(self, tmp_path: Path):
        assert collect_local_tool_trace_ids(str(tmp_path)) == []

    def test_extracts_trace_ids(self, tmp_path: Path):
        log = tmp_path / "run.log"
        trace_id = "0123456789abcdef0123456789abcdef"
        log.write_text(
            f"Some log line\n[local-toolkit-trace-id: {trace_id}]\n"
            f"Another line [local-toolkit-trace-id: {trace_id}]\n",
            encoding="utf-8",
        )
        result = collect_local_tool_trace_ids(str(tmp_path))
        assert result == [trace_id]  # deduplicated

    def test_multiple_unique_ids(self, tmp_path: Path):
        log = tmp_path / "run.log"
        id_a = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        id_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        log.write_text(
            f"[local-toolkit-trace-id: {id_a}]\n"
            f"[local-toolkit-trace-id: {id_b}]\n",
            encoding="utf-8",
        )
        result = collect_local_tool_trace_ids(str(tmp_path))
        assert result == [id_a, id_b]  # sorted

    def test_non_hex_32_not_matched(self, tmp_path: Path):
        log = tmp_path / "run.log"
        log.write_text("[local-toolkit-trace-id: not-a-valid-id]\n", encoding="utf-8")
        result = collect_local_tool_trace_ids(str(tmp_path))
        assert result == []

    def test_malformed_log_returns_empty(self, tmp_path: Path):
        log = tmp_path / "run.log"
        log.write_text("just random text no pattern\n", encoding="utf-8")
        result = collect_local_tool_trace_ids(str(tmp_path))
        assert result == []
