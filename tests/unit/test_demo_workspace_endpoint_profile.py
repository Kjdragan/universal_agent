"""Tests for endpoint_profile generalization (PR 14)."""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.services.demo_workspace import (
    ENDPOINT_PROFILE_ANTHROPIC,
    ENDPOINT_PROFILE_GEMINI,
    ENDPOINT_PROFILE_NONE,
    ENDPOINT_PROFILE_OPENAI,
    PROFILE_REQUIRED_ENV,
    VALID_ENDPOINT_PROFILES,
    detect_endpoint_profile,
    provision_demo_workspace,
    read_endpoint_profile,
)


# ── Profile vocabulary ──────────────────────────────────────────────────────


def test_profile_constants_are_distinct():
    assert len(set(VALID_ENDPOINT_PROFILES)) == 4
    assert ENDPOINT_PROFILE_ANTHROPIC in VALID_ENDPOINT_PROFILES
    assert ENDPOINT_PROFILE_GEMINI in VALID_ENDPOINT_PROFILES
    assert ENDPOINT_PROFILE_OPENAI in VALID_ENDPOINT_PROFILES
    assert ENDPOINT_PROFILE_NONE in VALID_ENDPOINT_PROFILES


def test_profile_required_env_covers_all_profiles():
    """Every valid profile must have an entry in PROFILE_REQUIRED_ENV."""
    for profile in VALID_ENDPOINT_PROFILES:
        assert profile in PROFILE_REQUIRED_ENV


def test_anthropic_profile_uses_oauth_not_env_var():
    """The Max plan OAuth path uses `claude /login`, not an env var."""
    assert PROFILE_REQUIRED_ENV[ENDPOINT_PROFILE_ANTHROPIC] == ""


def test_gemini_profile_requires_gemini_api_key():
    assert PROFILE_REQUIRED_ENV[ENDPOINT_PROFILE_GEMINI] == "GEMINI_API_KEY"


def test_openai_profile_requires_openai_api_key():
    assert PROFILE_REQUIRED_ENV[ENDPOINT_PROFILE_OPENAI] == "OPENAI_API_KEY"


# ── Provisioning with profile ───────────────────────────────────────────────


def test_provision_default_profile_is_anthropic_native(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos"))
    result = provision_demo_workspace("test-default")
    assert result.endpoint_profile == ENDPOINT_PROFILE_ANTHROPIC
    # Marker file exists in the workspace.
    assert (result.workspace_dir / ".endpoint_profile").exists()
    assert (result.workspace_dir / ".endpoint_profile").read_text(encoding="utf-8").strip() == "anthropic_native"


def test_provision_records_explicit_profile(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos"))
    result = provision_demo_workspace(
        "test-gemini",
        endpoint_profile=ENDPOINT_PROFILE_GEMINI,
    )
    assert result.endpoint_profile == ENDPOINT_PROFILE_GEMINI
    assert (result.workspace_dir / ".endpoint_profile").read_text(encoding="utf-8").strip() == "gemini_native"


def test_provision_normalizes_profile_case(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos"))
    result = provision_demo_workspace(
        "test-case",
        endpoint_profile="OPENAI_NATIVE",
    )
    assert result.endpoint_profile == ENDPOINT_PROFILE_OPENAI


def test_provision_rejects_unknown_profile(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos"))
    with pytest.raises(ValueError):
        provision_demo_workspace("test-bad", endpoint_profile="azure_native")


def test_provision_empty_profile_falls_back_to_default(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos"))
    result = provision_demo_workspace("test-empty", endpoint_profile="")
    assert result.endpoint_profile == ENDPOINT_PROFILE_ANTHROPIC


def test_provision_settings_shape_unchanged_across_profiles(tmp_path: Path, monkeypatch):
    """All profiles use the SAME vanilla settings.json — the profile is metadata."""
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos"))
    anthropic = provision_demo_workspace("a", endpoint_profile=ENDPOINT_PROFILE_ANTHROPIC)
    gemini = provision_demo_workspace("g", endpoint_profile=ENDPOINT_PROFILE_GEMINI)
    openai_ = provision_demo_workspace("o", endpoint_profile=ENDPOINT_PROFILE_OPENAI)
    none_ = provision_demo_workspace("n", endpoint_profile=ENDPOINT_PROFILE_NONE)
    # Same settings.json content across all profiles.
    contents = {
        anthropic.settings_path.read_text(encoding="utf-8"),
        gemini.settings_path.read_text(encoding="utf-8"),
        openai_.settings_path.read_text(encoding="utf-8"),
        none_.settings_path.read_text(encoding="utf-8"),
    }
    assert len(contents) == 1


def test_provision_to_dict_includes_profile(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos"))
    result = provision_demo_workspace("test-dict", endpoint_profile=ENDPOINT_PROFILE_GEMINI)
    payload = result.to_dict()
    assert payload["endpoint_profile"] == "gemini_native"


# ── read_endpoint_profile ───────────────────────────────────────────────────


def test_read_profile_from_provisioned_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos"))
    result = provision_demo_workspace("test-read", endpoint_profile=ENDPOINT_PROFILE_OPENAI)
    assert read_endpoint_profile(result.workspace_dir) == ENDPOINT_PROFILE_OPENAI


def test_read_profile_defaults_to_anthropic_when_marker_missing(tmp_path: Path):
    """Workspaces provisioned before PR 14 don't have the marker."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert read_endpoint_profile(workspace) == ENDPOINT_PROFILE_ANTHROPIC


def test_read_profile_falls_back_when_marker_invalid(tmp_path: Path):
    """A corrupted marker shouldn't break workspace handling."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".endpoint_profile").write_text("garbage_value\n", encoding="utf-8")
    assert read_endpoint_profile(workspace) == ENDPOINT_PROFILE_ANTHROPIC


# ── Topic detection ────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "Build agents with Claude Agent SDK",
    "Tutorial: Anthropic API basics",
    "Working with claude-code skills",
    "Using the @anthropic-ai/sdk in Node",
])
def test_detect_anthropic_topics(text):
    assert detect_endpoint_profile(text=text) == ENDPOINT_PROFILE_ANTHROPIC


@pytest.mark.parametrize("text", [
    "OpenAI Agents SDK getting started",
    "Build with GPT-4 function calling",
    "@openai/agents tutorial",
    "Codex CLI walkthrough",
])
def test_detect_openai_topics(text):
    assert detect_endpoint_profile(text=text) == ENDPOINT_PROFILE_OPENAI


@pytest.mark.parametrize("text", [
    "Build with Gemini 1.5 Flash",
    "google-genai SDK getting started",
    "Vertex AI deployment guide",
])
def test_detect_gemini_topics(text):
    assert detect_endpoint_profile(text=text) == ENDPOINT_PROFILE_GEMINI


@pytest.mark.parametrize("text", [
    "Build a FastAPI app",
    "Python data analysis tutorial",
    "How to use git effectively",
    "",
])
def test_detect_generic_topics_return_none(text):
    assert detect_endpoint_profile(text=text) == ENDPOINT_PROFILE_NONE


def test_detect_anthropic_wins_when_both_present():
    """A comparison-content tutorial mentioning both Claude and Gemini routes
    to anthropic_native (priority order). Caller can override if needed."""
    text = "Comparing Claude Agent SDK vs Gemini for agentic workflows"
    assert detect_endpoint_profile(text=text) == ENDPOINT_PROFILE_ANTHROPIC


def test_detect_uses_links_when_text_is_neutral():
    text = "Tutorial walkthrough"
    links = ["https://docs.anthropic.com/en/docs/agents-and-tools/skills"]
    assert detect_endpoint_profile(text=text, links=links) == ENDPOINT_PROFILE_ANTHROPIC


def test_detect_handles_none_links():
    assert detect_endpoint_profile(text="Claude tutorial", links=None) == ENDPOINT_PROFILE_ANTHROPIC


def test_detect_case_insensitive():
    assert detect_endpoint_profile(text="ANTHROPIC TUTORIAL") == ENDPOINT_PROFILE_ANTHROPIC
    assert detect_endpoint_profile(text="Building with OPENAI GPT-5") == ENDPOINT_PROFILE_OPENAI
