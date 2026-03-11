from __future__ import annotations

import re
from pathlib import Path

from universal_agent import prompt_assets


SKILL_PATH = Path(".claude/skills/notebooklm-orchestration/SKILL.md")
AGENT_PATH = Path(".claude/agents/notebooklm-operator.md")


def _frontmatter(text: str) -> str:
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S)
    assert match, "missing frontmatter"
    return match.group(1)


def test_notebooklm_skill_exists_and_has_expected_routing_contract():
    text = SKILL_PATH.read_text(encoding="utf-8")
    frontmatter = _frontmatter(text)

    assert "name: notebooklm-orchestration" in frontmatter
    assert "Task(subagent_type='notebooklm-operator'" in text
    assert "MCP first" in text
    assert "Confirm-Before-Action Guardrails" in text


def test_notebooklm_agent_exists_and_has_output_contract():
    text = AGENT_PATH.read_text(encoding="utf-8")
    frontmatter = _frontmatter(text)

    assert "name: notebooklm-operator" in frontmatter
    assert "tools:" in frontmatter
    assert "## Output Contract" in text
    assert "path_used" in text


def test_notebooklm_skill_is_discoverable():
    skills = prompt_assets.discover_skills(".claude/skills")
    names = {s.get("name") for s in skills}
    assert "notebooklm-orchestration" in names


def test_live_capabilities_snapshot_mentions_notebooklm_assets():
    snapshot = prompt_assets.build_live_capabilities_snapshot(str(Path.cwd()))
    assert "notebooklm-operator" in snapshot
    assert "notebooklm-orchestration" in snapshot
