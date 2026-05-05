"""Tests for the Phase 0 dependency currency module (PR 6a)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.services.dependency_currency import (
    ANTHROPIC_ADJACENT_PACKAGES,
    OutdatedPackage,
    SweepReport,
    append_release_timeline_entry,
    assemble_sweep_report,
    compare_versions,
    detect_release_announcement,
    is_anthropic_adjacent,
    parse_claude_version,
    parse_npm_outdated,
    parse_uv_outdated,
    record_upgrade_failure,
    write_installed_versions_page,
    write_sweep_artifacts,
    write_version_drift_page,
)


# ── Anthropic-adjacency ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "claude-agent-sdk",
        "anthropic",
        "@anthropic-ai/sdk",
        "@anthropic-ai/claude-agent-sdk",
        "claude-code",
        "claude",
        "@anthropic-ai/some-future-package",
    ],
)
def test_is_anthropic_adjacent_recognizes_known_packages(name: str):
    assert is_anthropic_adjacent(name) is True


@pytest.mark.parametrize(
    "name",
    ["openai", "fastapi", "react", "@openai/agents", "", None, "  "],
)
def test_is_anthropic_adjacent_rejects_others(name):
    assert is_anthropic_adjacent(name) is False


def test_anthropic_packages_constant_contains_critical_entries():
    """If someone refactors the constant, these must remain. They gate demos."""
    for required in ("claude-code", "anthropic", "claude-agent-sdk", "@anthropic-ai/sdk"):
        assert required in ANTHROPIC_ADJACENT_PACKAGES


# ── Version comparison ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("1.0.0", "1.0.0", 0),
        ("1.0.0", "1.0.1", -1),
        ("1.0.1", "1.0.0", 1),
        ("1.2.0", "2.0.0", -1),
        ("2.1.116", "2.1.115", 1),
        ("v1.0.0", "1.0.0", 0),  # leading 'v' tolerated
        ("0.5.0-beta", "0.5.0", 0),  # pre-release suffix dropped for ordering
        ("", "1.0.0", -1),
        ("1.0.0", "", 1),
        ("garbage", "1.0.0", -1),
    ],
)
def test_compare_versions(a, b, expected):
    assert compare_versions(a, b) == expected


# ── uv pip list --outdated parsing ──────────────────────────────────────────


def test_parse_uv_outdated_modern_format():
    payload = json.dumps(
        [
            {"name": "claude-agent-sdk", "version": "0.4.0", "latest_version": "0.5.1"},
            {"name": "fastapi", "version": "0.115.0", "latest_version": "0.115.6"},
        ]
    )
    pkgs = parse_uv_outdated(payload)
    assert len(pkgs) == 2
    sdk = next(p for p in pkgs if p.name == "claude-agent-sdk")
    assert sdk.is_anthropic_adjacent is True
    assert sdk.installed == "0.4.0"
    assert sdk.latest == "0.5.1"
    assert sdk.needs_upgrade is True
    fastapi = next(p for p in pkgs if p.name == "fastapi")
    assert fastapi.is_anthropic_adjacent is False


def test_parse_uv_outdated_legacy_latest_field():
    payload = json.dumps([{"name": "anthropic", "version": "0.50.0", "latest": "0.75.0"}])
    pkgs = parse_uv_outdated(payload)
    assert len(pkgs) == 1
    assert pkgs[0].latest == "0.75.0"


def test_parse_uv_outdated_handles_garbage():
    assert parse_uv_outdated("") == []
    assert parse_uv_outdated("not json") == []
    assert parse_uv_outdated("{}") == []


# ── npm outdated parsing ────────────────────────────────────────────────────


def test_parse_npm_outdated():
    payload = json.dumps(
        {
            "@anthropic-ai/sdk": {"current": "0.30.0", "wanted": "0.32.0", "latest": "0.32.0"},
            "react": {"current": "18.3.0", "wanted": "18.3.1", "latest": "19.0.0"},
        }
    )
    pkgs = parse_npm_outdated(payload)
    names = {p.name for p in pkgs}
    assert "@anthropic-ai/sdk" in names
    sdk = next(p for p in pkgs if p.name == "@anthropic-ai/sdk")
    assert sdk.is_anthropic_adjacent is True
    assert sdk.latest == "0.32.0"


def test_parse_npm_outdated_handles_garbage():
    assert parse_npm_outdated("") == []
    assert parse_npm_outdated("not json") == []


# ── Claude CLI version parsing ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "stdout,expected",
    [
        ("claude 2.1.116\n", "2.1.116"),
        ("Claude Code 1.5.0", "1.5.0"),
        ("v0.9.2-beta", "0.9.2-beta"),
        ("", ""),
        ("no version anywhere", ""),
    ],
)
def test_parse_claude_version(stdout, expected):
    assert parse_claude_version(stdout) == expected


# ── Release-announcement detection ──────────────────────────────────────────


def test_detect_release_announcement_matches_known_package():
    info = detect_release_announcement(
        text="Claude Code 2.1.116 is out with new skills support",
        links=[],
    )
    assert info is not None
    assert info["package"] == "claude-code"
    assert info["version"] == "2.1.116"
    assert info["is_anthropic_adjacent"] is True


def test_detect_release_announcement_picks_up_links():
    info = detect_release_announcement(
        text="New release",
        links=["https://github.com/anthropics/claude-agent-sdk/releases/tag/v0.5.1"],
    )
    assert info is not None
    assert info["package"] == "claude-agent-sdk"
    assert info["version"] == "0.5.1"


def test_detect_release_announcement_returns_none_without_package():
    assert detect_release_announcement(text="Just a tweet", links=[]) is None


def test_detect_release_announcement_returns_none_without_version():
    assert detect_release_announcement(text="claude-agent-sdk is great!", links=[]) is None


# ── Vault writers ───────────────────────────────────────────────────────────


def test_write_installed_versions_page(tmp_path: Path):
    target = write_installed_versions_page(
        tmp_path,
        python_packages={"anthropic": "0.75.0", "fastapi": "0.115.6"},
        npm_packages={"@anthropic-ai/sdk": "0.32.0"},
        claude_cli_version="2.1.116",
    )
    text = target.read_text(encoding="utf-8")
    assert "claude: `2.1.116`" in text
    # Anthropic-adjacent gets a marker.
    assert "anthropic: `0.75.0` (Anthropic)" in text
    assert "@anthropic-ai/sdk: `0.32.0` (Anthropic)" in text
    # Non-adjacent does not get the marker.
    assert "fastapi: `0.115.6`" in text
    assert "fastapi: `0.115.6` (Anthropic)" not in text


def test_write_version_drift_page_separates_anthropic_section(tmp_path: Path):
    outdated = [
        OutdatedPackage(
            name="claude-agent-sdk",
            ecosystem="pypi",
            installed="0.4.0",
            latest="0.5.1",
            is_anthropic_adjacent=True,
        ),
        OutdatedPackage(
            name="fastapi",
            ecosystem="pypi",
            installed="0.115.0",
            latest="0.115.6",
            is_anthropic_adjacent=False,
        ),
    ]
    target = write_version_drift_page(tmp_path, outdated=outdated)
    text = target.read_text(encoding="utf-8")
    anth_idx = text.find("Anthropic-adjacent")
    other_idx = text.find("Other packages")
    assert anth_idx >= 0
    assert other_idx > anth_idx, "Anthropic-adjacent section must come before others"
    assert "claude-agent-sdk" in text
    assert "fastapi" in text


def test_write_version_drift_page_handles_empty(tmp_path: Path):
    target = write_version_drift_page(tmp_path, outdated=[])
    text = target.read_text(encoding="utf-8")
    assert "All tracked packages are current." in text


def test_append_release_timeline_entry(tmp_path: Path):
    target = append_release_timeline_entry(
        tmp_path,
        package="claude-code",
        version="2.1.116",
        source_url="https://github.com/anthropics/claude-code/releases/tag/v2.1.116",
        notable_features=["skills support", "post-mortem fixes"],
    )
    text = target.read_text(encoding="utf-8")
    assert "claude-code `2.1.116`" in text
    assert "skills support" in text
    assert "post-mortem fixes" in text


def test_append_release_timeline_entry_appends_not_overwrites(tmp_path: Path):
    append_release_timeline_entry(tmp_path, package="anthropic", version="0.75.0")
    append_release_timeline_entry(tmp_path, package="anthropic", version="0.76.0")
    text = (tmp_path / "infrastructure" / "release_timeline.md").read_text(encoding="utf-8")
    assert "0.75.0" in text and "0.76.0" in text


def test_record_upgrade_failure_writes_per_failure_file(tmp_path: Path):
    target = record_upgrade_failure(
        tmp_path,
        package="claude-agent-sdk",
        from_version="0.4.0",
        to_version="0.5.1",
        error_summary="ImportError: cannot import name 'foo' from 'claude_agent_sdk'",
    )
    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert "claude-agent-sdk" in text
    assert "from_version: `0.4.0`" in text
    assert "to_version: `0.5.1`" in text
    assert "ImportError" in text


# ── End-to-end sweep assembly ────────────────────────────────────────────────


def test_assemble_sweep_report_from_real_outputs():
    uv_json = json.dumps(
        [{"name": "anthropic", "version": "0.74.0", "latest_version": "0.75.0"}]
    )
    npm_json = json.dumps(
        {"@anthropic-ai/sdk": {"current": "0.30.0", "wanted": "0.32.0", "latest": "0.32.0"}}
    )
    claude_text = "claude 2.1.116"
    report = assemble_sweep_report(
        uv_outdated_json=uv_json,
        npm_outdated_json=npm_json,
        claude_version_stdout=claude_text,
    )
    assert isinstance(report, SweepReport)
    assert report.claude_cli_version == "2.1.116"
    assert len(report.pypi_outdated) == 1
    assert len(report.npm_outdated) == 1
    assert len(report.anthropic_outdated) == 2  # both pkgs are Anthropic
    assert report.to_dict()["summary"]["anthropic_outdated"] == 2


def test_write_sweep_artifacts_writes_both_pages(tmp_path: Path):
    report = assemble_sweep_report(
        uv_outdated_json=json.dumps(
            [{"name": "claude-agent-sdk", "version": "0.4.0", "latest_version": "0.5.1"}]
        ),
        npm_outdated_json="",
        claude_version_stdout="claude 2.1.116",
    )
    paths = write_sweep_artifacts(
        report,
        vault_path=tmp_path,
        installed_pypi={"claude-agent-sdk": "0.4.0"},
    )
    assert (tmp_path / "infrastructure" / "installed_versions.md").exists()
    assert (tmp_path / "infrastructure" / "version_drift.md").exists()
    assert "installed_versions" in paths
    assert "version_drift" in paths
