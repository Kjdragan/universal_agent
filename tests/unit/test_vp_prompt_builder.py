"""Tests for the VP-specific system prompt builder.

Covers: build_vp_system_prompt(), _load_mission_briefing()
"""
from __future__ import annotations

import os
from pathlib import Path

from universal_agent.prompt_builder import build_vp_system_prompt


def test_vp_prompt_includes_soul_context():
    prompt = build_vp_system_prompt(
        workspace_path="/tmp/test_workspace",
        soul_context="You are CODIE, the VP Coder Agent.",
    )
    assert "CODIE" in prompt
    assert "VP Coder Agent" in prompt


def test_vp_prompt_includes_mission_briefing(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "MISSION_BRIEFING.md").write_text(
        "## Doc Maintenance\nFix drift issues in docs/.",
        encoding="utf-8",
    )
    prompt = build_vp_system_prompt(
        workspace_path=str(workspace),
        soul_context="ATLAS soul",
    )
    assert "MISSION BRIEFING" in prompt
    assert "Fix drift issues" in prompt


def test_vp_prompt_excludes_simone_sections():
    """VP prompt must NOT contain Simone-specific coordinator sections."""
    prompt = build_vp_system_prompt(
        workspace_path="/tmp/test_workspace",
        soul_context="CODIE soul block",
        memory_context="memory block",
        capabilities_content="caps block",
    )
    # These are Simone-specific sections that should be stripped:
    simone_markers = [
        "COORDINATOR ROLE",
        "SHOWCASE / OPEN-ENDED",
        "SEARCH HYGIENE",
        "DATA FLOW POLICY",
        "WORKBENCH RESTRICTIONS",
        "ARTIFACT OUTPUT POLICY",
        "EMAIL / COMMUNICATION",
        "TASK QUEUE EXECUTION",
        "REPORT DELEGATION",
        "SYSTEM CONFIG DELEGATION",
    ]
    for marker in simone_markers:
        assert marker not in prompt, f"Simone section '{marker}' leaked into VP prompt"


def test_vp_prompt_includes_required_sections():
    """VP prompt must include essential operational sections."""
    prompt = build_vp_system_prompt(
        workspace_path="/tmp/test_workspace",
        soul_context="ATLAS autonomous agent soul",
        memory_context="persistent memory block",
        capabilities_content="### Capabilities\n- research-specialist",
        skills_xml="<skills>vp-orchestration</skills>",
    )
    assert "Current Date:" in prompt
    assert "TEMPORAL CONTEXT" in prompt
    assert "ARCHITECTURE & TOOL USAGE" in prompt
    assert "CAPABILITY DOMAINS" in prompt
    assert "ZAI VISION" in prompt
    assert "AUTONOMOUS BEHAVIOR" in prompt
    assert "SECRETS (INFISICAL)" in prompt
    assert "MEMORY MANAGEMENT" in prompt
    assert "MEMORY CONTEXT" in prompt
    assert "CURRENT_SESSION_WORKSPACE" in prompt
    # Note: skills_xml is folded into the CAPABILITY DOMAINS section now,
    # not rendered as a standalone "SKILLS" heading.
    assert "research-specialist" in prompt


def test_vp_prompt_size_regression():
    """VP prompt must be substantially smaller than Simone's full prompt.

    Guard against prompt bloat: VP prompt should stay under 30K chars.
    """
    prompt = build_vp_system_prompt(
        workspace_path="/tmp/test_workspace",
        soul_context="CODIE autonomous coding agent soul " * 10,
        memory_context="memory entry " * 20,
        capabilities_content="### Capabilities\n" + "- tool\n" * 50,
        skills_xml="<skills><skill>vp-orchestration</skill></skills>",
    )
    assert len(prompt) < 30_000, (
        f"VP prompt is {len(prompt)} chars — exceeds 30K regression threshold. "
        f"VP prompts should be ~20K chars, not ~90K like Simone's."
    )


def test_mission_briefing_truncation(tmp_path: Path):
    """Briefings exceeding max_chars are truncated with '...' suffix."""
    from universal_agent.prompt_builder import _load_mission_briefing

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Write a briefing that exceeds the default 4000 char limit
    (workspace / "MISSION_BRIEFING.md").write_text(
        "X" * 5000,
        encoding="utf-8",
    )
    result = _load_mission_briefing(str(workspace), max_chars=4000)
    assert result.endswith("...")
    # The result includes the header + content, so total should be <= header + 4000
    content_portion = result.split("\n\n", 1)[-1]
    assert len(content_portion) <= 4000


def test_mission_briefing_absent_gracefully(tmp_path: Path):
    """Missing MISSION_BRIEFING.md returns empty string without error."""
    from universal_agent.prompt_builder import _load_mission_briefing

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = _load_mission_briefing(str(workspace))
    assert result == ""
