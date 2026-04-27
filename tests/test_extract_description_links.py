"""Tests for extract_description_links.py — YouTube description link extraction and classification.

Uses the real marimo video (bMoNOb0iXpA) description as the canonical test case:
- 2 high-value links (Kaggle competition + GitHub repo)
- 8 social/promo links (Discord, Reddit, Twitter, etc.)
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "youtube-tutorial-creation" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from extract_description_links import (
    classify_url,
    extract_urls,
    classify_and_filter,
)

# ---------------------------------------------------------------------------
# Real test data — the actual marimo video description
# ---------------------------------------------------------------------------

MARIMO_DESCRIPTION = textwrap.dedent("""\
    We gave the auto research technique a spin. It seems to work, which is exciting, but your mileage might vary. We're going to see why by trying to get it to tackle a Kaggle problem from a few years ago.

    Link to Kaggle Problem:
    https://www.kaggle.com/competitions/traveling-santa-2018-prime-paths/overview

    Link to (dirty, very dirty) vibe-coded repo:
    https://github.com/koaning/auto-tsp

    00:00 Introduction
    00:41 The Kaggle Problem
    01:36 How Auto-Research is Setup
    04:27 Algo notebook
    06:34 Progress
    08:58 Leaderboard

    Links:
    Website: https://marimo.io
    Discord: https://marimo.io/discord
    Reddit: https://www.reddit.com/r/marimo_notebook/
    Twitter: https://x.com/@marimo_io
    Tiktok: https://www.tiktok.com/@marimo.io
    Instagram: https://www.instagram.com/marimo_io
    Bluesky: https://bsky.app/profile/marimo.io
    Newsletter: https://marimo.io/newsletter
""")


# ===================================================================
# URL Extraction Tests
# ===================================================================


class TestExtractUrls:
    """Test URL extraction from free-form text."""

    def test_extracts_urls_from_real_description(self):
        urls = extract_urls(MARIMO_DESCRIPTION)
        assert len(urls) >= 10  # 2 high-value + 8+ social/promo

    def test_extracts_github_url(self):
        urls = extract_urls(MARIMO_DESCRIPTION)
        assert "https://github.com/koaning/auto-tsp" in urls

    def test_extracts_kaggle_url(self):
        urls = extract_urls(MARIMO_DESCRIPTION)
        assert "https://www.kaggle.com/competitions/traveling-santa-2018-prime-paths/overview" in urls

    def test_extracts_social_urls(self):
        urls = extract_urls(MARIMO_DESCRIPTION)
        social_domains = {"x.com", "reddit.com", "tiktok.com", "instagram.com", "bsky.app"}
        found = {u for u in urls if any(d in u for d in social_domains)}
        assert len(found) >= 5

    def test_empty_description_returns_empty(self):
        assert extract_urls("") == []
        assert extract_urls(None) == []

    def test_no_urls_in_text(self):
        assert extract_urls("This is a plain text description with no links.") == []

    def test_deduplicates_urls(self):
        text = "Visit https://example.com and again https://example.com"
        urls = extract_urls(text)
        assert urls.count("https://example.com") == 1


# ===================================================================
# URL Classification Tests
# ===================================================================


class TestClassifyUrl:
    """Test individual URL classification."""

    # GitHub
    def test_github_repo(self):
        assert classify_url("https://github.com/koaning/auto-tsp") == "github_repo"

    def test_github_repo_with_trailing_slash(self):
        assert classify_url("https://github.com/user/repo/") == "github_repo"

    def test_github_subpath_still_repo(self):
        assert classify_url("https://github.com/user/repo/tree/main") == "github_repo"

    def test_gitlab_repo(self):
        assert classify_url("https://gitlab.com/user/project") == "github_repo"

    # Kaggle
    def test_kaggle_competition(self):
        assert classify_url("https://www.kaggle.com/competitions/traveling-santa-2018-prime-paths/overview") == "kaggle_competition"

    def test_kaggle_dataset(self):
        assert classify_url("https://www.kaggle.com/datasets/some-dataset") == "kaggle_dataset"

    # Documentation
    def test_readthedocs(self):
        assert classify_url("https://pandas.pydata.org/docs/") == "documentation"

    def test_readthedocs_io(self):
        assert classify_url("https://some-library.readthedocs.io/en/latest/") == "documentation"

    # Social / promo
    def test_twitter(self):
        assert classify_url("https://x.com/@marimo_io") == "social"

    def test_reddit(self):
        assert classify_url("https://www.reddit.com/r/marimo_notebook/") == "social"

    def test_discord(self):
        assert classify_url("https://discord.gg/something") == "social"

    def test_discord_via_redirect(self):
        assert classify_url("https://marimo.io/discord") == "social"

    def test_instagram(self):
        assert classify_url("https://www.instagram.com/marimo_io") == "social"

    def test_tiktok(self):
        assert classify_url("https://www.tiktok.com/@marimo.io") == "social"

    def test_bluesky(self):
        assert classify_url("https://bsky.app/profile/marimo.io") == "social"

    def test_youtube_self_reference(self):
        assert classify_url("https://www.youtube.com/watch?v=abc123") == "social"

    def test_newsletter(self):
        # Newsletter URLs from project homepages should be social
        assert classify_url("https://marimo.io/newsletter") == "social"

    # Hugging Face
    def test_huggingface(self):
        assert classify_url("https://huggingface.co/datasets/some-dataset") == "dataset"

    # Other
    def test_generic_url(self):
        assert classify_url("https://example.com/some/page") == "other"

    def test_bare_homepage(self):
        # Project homepages without a doc-like path
        assert classify_url("https://marimo.io") == "other"


# ===================================================================
# Classify and Filter Tests (Integration)
# ===================================================================


class TestClassifyAndFilter:
    """Test the combined extraction + classification pipeline."""

    def test_real_description_produces_correct_counts(self):
        result = classify_and_filter(MARIMO_DESCRIPTION)
        high_value = [r for r in result if r["type"] not in ("social", "other")]
        social = [r for r in result if r["type"] == "social"]
        assert len(high_value) == 2, f"Expected 2 high-value links, got {len(high_value)}: {high_value}"
        assert len(social) >= 5, f"Expected 5+ social links, got {len(social)}"

    def test_high_value_links_identified_correctly(self):
        result = classify_and_filter(MARIMO_DESCRIPTION)
        high_value = {r["url"]: r["type"] for r in result if r["type"] not in ("social", "other")}
        assert "https://github.com/koaning/auto-tsp" in high_value
        assert high_value["https://github.com/koaning/auto-tsp"] == "github_repo"
        assert "https://www.kaggle.com/competitions/traveling-santa-2018-prime-paths/overview" in high_value
        assert high_value["https://www.kaggle.com/competitions/traveling-santa-2018-prime-paths/overview"] == "kaggle_competition"

    def test_empty_description(self):
        result = classify_and_filter("")
        assert result == []

    def test_social_only_description(self):
        text = "Follow us: https://twitter.com/acme https://discord.gg/acme"
        result = classify_and_filter(text)
        high_value = [r for r in result if r["type"] not in ("social", "other")]
        assert len(high_value) == 0

    def test_max_links_cap(self):
        """When max_links is set, only that many high-value links should be returned."""
        result = classify_and_filter(MARIMO_DESCRIPTION, max_high_value=1)
        high_value = [r for r in result if r["type"] not in ("social", "other")]
        assert len(high_value) <= 1


# ===================================================================
# Script Self-Test (subprocess)
# ===================================================================


class TestScriptExecution:
    """Test the script can be executed as a subprocess."""

    def test_self_test_flag(self):
        """--self-test should exit 0 without errors."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "extract_description_links.py"), "--self-test"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Self-test failed: {result.stderr}"

    def test_dry_run_with_description(self):
        """--dry-run should classify without fetching."""
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "extract_description_links.py"),
                "--description", MARIMO_DESCRIPTION,
                "--dry-run",
                "--report-json", "/tmp/test_description_links_report.json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Dry run failed: {result.stderr}"
        report = json.loads(Path("/tmp/test_description_links_report.json").read_text())
        assert "links" in report
        assert len(report["links"]) >= 2
        # In dry-run, nothing should be fetched
        for link in report["links"]:
            assert link.get("fetched") is False
