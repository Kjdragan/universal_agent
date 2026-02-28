"""
Tests for the Scrapling inbox scraper module.

Uses mocks for the actual network fetching to keep tests fast and
environment-independent.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import directly from the subpackage to avoid the tools/__init__.py chain
from src.universal_agent.tools.scrapling_scraper import (
    FetcherLevel,
    InboxProcessor,
    page_to_markdown,
)
from src.universal_agent.tools.scrapling_scraper.fetcher_strategy import (
    FetcherStrategy,
    ScrapeRequest,
    _is_bot_blocked,
)
from src.universal_agent.tools.scrapling_scraper.inbox_processor import (
    _load_job,
    _url_to_filename,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_inbox(tmp_path: Path) -> Path:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return inbox


@pytest.fixture()
def tmp_output(tmp_path: Path) -> Path:
    out = tmp_path / "processed"
    out.mkdir()
    return out


def _make_page(body: str = "<html><head><title>Test</title></head><body><p>Hello</p></body></html>", status: int = 200) -> MagicMock:
    """Create a minimal mock Scrapling page."""
    page = MagicMock()
    page.status = status
    page.body = body.encode()
    page.text = body

    # css_first
    def css_first(selector):
        if "title" in selector:
            m = MagicMock()
            m.__str__ = lambda s: "Test Page"
            return m
        if 'meta[name="description"]' in selector:
            m = MagicMock()
            m.attrib = {"content": "A test description"}
            return m
        return None

    page.css_first.side_effect = css_first

    # css (returns list)
    page.css.return_value = []

    # get_all_text
    page.get_all_text.return_value = "Hello world content here"

    return page


# ---------------------------------------------------------------------------
# _is_bot_blocked
# ---------------------------------------------------------------------------

class TestIsBotBlocked:
    def test_clean_page_not_blocked(self):
        page = _make_page("<html><body>Real content</body></html>", status=200)
        assert _is_bot_blocked(page) is False

    def test_403_is_blocked(self):
        page = _make_page(status=403)
        assert _is_bot_blocked(page) is True

    def test_cloudflare_challenge_detected(self):
        page = _make_page("<html>just a moment...</html>", status=200)
        assert _is_bot_blocked(page) is True

    def test_turnstile_detected(self):
        page = _make_page("<html>cf-browser-verification active</html>")
        assert _is_bot_blocked(page) is True

    def test_503_is_blocked(self):
        page = _make_page(status=503)
        assert _is_bot_blocked(page) is True


# ---------------------------------------------------------------------------
# _url_to_filename
# ---------------------------------------------------------------------------

class TestUrlToFilename:
    def test_basic_url(self):
        from src.universal_agent.tools.scrapling_scraper.inbox_processor import _url_to_filename
        name = _url_to_filename("https://example.com/some/path")
        assert "example.com" in name
        assert "/" not in name

    def test_no_path(self):
        from src.universal_agent.tools.scrapling_scraper.inbox_processor import _url_to_filename
        name = _url_to_filename("https://example.com")
        assert "example.com" in name
        assert len(name) > 0

    def test_long_url_is_truncated(self):
        from src.universal_agent.tools.scrapling_scraper.inbox_processor import _url_to_filename
        long_url = "https://example.com/" + "x" * 300
        name = _url_to_filename(long_url)
        assert len(name) <= 120


# ---------------------------------------------------------------------------
# _load_job
# ---------------------------------------------------------------------------

class TestLoadJob:
    def test_load_list_format(self, tmp_path):
        p = tmp_path / "job.json"
        p.write_text(json.dumps(["https://a.com", "https://b.com"]))
        job = _load_job(p)
        assert job.urls == ["https://a.com", "https://b.com"]
        assert job.options.min_level == FetcherLevel.BASIC

    def test_load_object_format(self, tmp_path):
        p = tmp_path / "job.json"
        data = {
            "urls": ["https://c.com"],
            "options": {
                "min_level": "stealthy",
                "solve_cloudflare": True,
                "timeout": 60,
            },
            "project": "my-research",
        }
        p.write_text(json.dumps(data))
        job = _load_job(p)
        assert job.urls == ["https://c.com"]
        assert job.options.min_level == FetcherLevel.STEALTHY
        assert job.options.timeout == 60.0
        assert job.metadata == {"project": "my-research"}

    def test_empty_urls_ok(self, tmp_path):
        p = tmp_path / "job.json"
        p.write_text(json.dumps({"urls": []}))
        job = _load_job(p)
        assert job.urls == []

    def test_invalid_json_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json")
        with pytest.raises(Exception):
            _load_job(p)


# ---------------------------------------------------------------------------
# page_to_markdown
# ---------------------------------------------------------------------------

class TestPageToMarkdown:
    def test_basic_output(self):
        page = _make_page()
        md = page_to_markdown(page, "https://example.com", fetcher_level="BASIC")
        assert "https://example.com" in md
        assert "BASIC" in md
        assert "## Metadata" in md

    def test_includes_job_metadata(self):
        page = _make_page()
        md = page_to_markdown(
            page,
            "https://example.com",
            job_metadata={"project": "test-run"},
        )
        assert "project" in md
        assert "test-run" in md


class TestFetcherStrategy:
    def test_blocked_partial_result_preserves_original_page_tier(self):
        strategy = FetcherStrategy(escalation_delay=0)
        blocked_page = _make_page("<html>access denied</html>", status=403)

        with patch(
            "src.universal_agent.tools.scrapling_scraper.fetcher_strategy._safe_fetch_basic",
            return_value=blocked_page,
        ) as mock_basic, patch(
            "src.universal_agent.tools.scrapling_scraper.fetcher_strategy._safe_fetch_dynamic",
            side_effect=RuntimeError("dynamic failed"),
        ), patch(
            "src.universal_agent.tools.scrapling_scraper.fetcher_strategy._safe_fetch_stealthy",
            side_effect=RuntimeError("stealthy failed"),
        ), patch(
            "src.universal_agent.tools.scrapling_scraper.fetcher_strategy._is_bot_blocked",
            return_value=True,
        ), patch(
            "src.universal_agent.tools.scrapling_scraper.fetcher_strategy.time.sleep",
            return_value=None,
        ):
            page, level = strategy.fetch(ScrapeRequest(url="https://example.com"))

        assert mock_basic.called
        assert page is blocked_page
        assert level == FetcherLevel.BASIC

    def test_blocked_force_level_prefers_forced_tier(self):
        strategy = FetcherStrategy(escalation_delay=0)
        blocked_page = _make_page("<html>blocked</html>", status=403)

        with patch(
            "src.universal_agent.tools.scrapling_scraper.fetcher_strategy._safe_fetch_basic"
        ) as mock_basic, patch(
            "src.universal_agent.tools.scrapling_scraper.fetcher_strategy._safe_fetch_dynamic",
            return_value=blocked_page,
        ) as mock_dynamic, patch(
            "src.universal_agent.tools.scrapling_scraper.fetcher_strategy._safe_fetch_stealthy"
        ) as mock_stealth, patch(
            "src.universal_agent.tools.scrapling_scraper.fetcher_strategy._is_bot_blocked",
            return_value=True,
        ), patch(
            "src.universal_agent.tools.scrapling_scraper.fetcher_strategy.time.sleep",
            return_value=None,
        ):
            page, level = strategy.fetch(
                ScrapeRequest(url="https://example.com", force_level=FetcherLevel.DYNAMIC)
            )

        assert not mock_basic.called
        assert mock_dynamic.called
        assert not mock_stealth.called
        assert page is blocked_page
        assert level == FetcherLevel.DYNAMIC


# ---------------------------------------------------------------------------
# InboxProcessor — integration (mocked fetching)
# ---------------------------------------------------------------------------

class TestInboxProcessor:
    def _make_processor(self, tmp_inbox, tmp_output, **kwargs):
        return InboxProcessor(
            inbox_dir=tmp_inbox,
            output_dir=tmp_output,
            per_url_delay=0,
            **kwargs,
        )

    def test_creates_subdirs(self, tmp_inbox, tmp_output):
        self._make_processor(tmp_inbox, tmp_output)
        assert (tmp_inbox / "processing").exists()
        assert (tmp_inbox / "done").exists()
        assert (tmp_inbox / "failed").exists()

    @patch(
        "src.universal_agent.tools.scrapling_scraper.fetcher_strategy._safe_fetch_basic"
    )
    def test_run_once_processes_json(self, mock_fetch, tmp_inbox, tmp_output):
        mock_fetch.return_value = _make_page()

        job_file = tmp_inbox / "job1.json"
        job_file.write_text(json.dumps(["https://example.com"]))

        processor = self._make_processor(tmp_inbox, tmp_output)
        summary = processor.run_once()

        assert summary["jobs_found"] == 1
        assert summary["urls_scraped"] == 1
        assert summary["urls_failed"] == 0
        # JSON moved to done/
        assert (tmp_inbox / "done" / "job1.json").exists()
        # Output .md written
        md_files = list(tmp_output.glob("*.md"))
        assert len(md_files) == 1

    @patch(
        "src.universal_agent.tools.scrapling_scraper.fetcher_strategy._safe_fetch_basic"
    )
    def test_empty_inbox_produces_no_output(self, mock_fetch, tmp_inbox, tmp_output):
        processor = self._make_processor(tmp_inbox, tmp_output)
        summary = processor.run_once()
        assert summary["jobs_found"] == 0
        assert summary["urls_scraped"] == 0

    @patch(
        "src.universal_agent.tools.scrapling_scraper.fetcher_strategy._safe_fetch_basic"
    )
    def test_skip_existing_output(self, mock_fetch, tmp_inbox, tmp_output):
        mock_fetch.return_value = _make_page()

        job_file = tmp_inbox / "job2.json"
        job_file.write_text(json.dumps(["https://example.com/page"]))

        processor = self._make_processor(tmp_inbox, tmp_output, overwrite=False)

        # Pre-create the output file
        existing_stem = _url_to_filename("https://example.com/page")
        (tmp_output / f"{existing_stem}.md").write_text("existing")

        summary = processor.run_once()
        assert summary["urls_skipped"] == 1
        assert summary["urls_scraped"] == 0
        assert mock_fetch.call_count == 0

    def test_bad_json_file_goes_to_failed(self, tmp_inbox, tmp_output):
        bad_file = tmp_inbox / "bad.json"
        bad_file.write_text("{ not valid json")

        processor = self._make_processor(tmp_inbox, tmp_output)
        summary = processor.run_once()

        assert summary["jobs_failed"] == 1
        assert (tmp_inbox / "failed" / "bad.json").exists()

    @patch(
        "src.universal_agent.tools.scrapling_scraper.fetcher_strategy._safe_fetch_basic"
    )
    def test_nested_json_files_found(self, mock_fetch, tmp_inbox, tmp_output):
        """JSON files in subdirectories of inbox should also be processed."""
        mock_fetch.return_value = _make_page()

        subdir = tmp_inbox / "batch_2026_02"
        subdir.mkdir()
        (subdir / "urls.json").write_text(json.dumps(["https://nested.example.com"]))

        processor = self._make_processor(tmp_inbox, tmp_output)
        summary = processor.run_once()

        assert summary["jobs_found"] == 1
        assert summary["urls_scraped"] == 1

    @patch(
        "src.universal_agent.tools.scrapling_scraper.fetcher_strategy._safe_fetch_basic"
    )
    def test_fetch_exception_writes_error_stub(self, mock_fetch, tmp_inbox, tmp_output):
        mock_fetch.side_effect = RuntimeError("Connection refused")

        job_file = tmp_inbox / "job_err.json"
        job_file.write_text(json.dumps(["https://broken.example.com"]))

        processor = self._make_processor(tmp_inbox, tmp_output)
        summary = processor.run_once()

        assert summary["urls_failed"] == 1
        md_files = list(tmp_output.glob("*.md"))
        assert len(md_files) == 1
        assert "Error" in md_files[0].read_text()
