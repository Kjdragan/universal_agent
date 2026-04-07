from __future__ import annotations

import re
from pathlib import Path

from universal_agent import prompt_assets


SKILL_PATH = Path(".claude/skills/llm-wiki-orchestration/SKILL.md")
AGENT_PATH = Path(".claude/agents/wiki-maintainer.md")


def _frontmatter(text: str) -> str:
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S)
    assert match, "missing frontmatter"
    return match.group(1)


def test_llm_wiki_skill_exists_and_routes_to_wiki_agent():
    text = SKILL_PATH.read_text(encoding="utf-8")
    frontmatter = _frontmatter(text)

    assert "name: llm-wiki-orchestration" in frontmatter
    assert "Task(subagent_type='wiki-maintainer'" in text
    assert "internal memory vault" in text.lower()
    assert "external knowledge vault" in text.lower()


def test_llm_wiki_agent_exists_and_has_output_contract():
    text = AGENT_PATH.read_text(encoding="utf-8")
    frontmatter = _frontmatter(text)

    assert "name: wiki-maintainer" in frontmatter
    assert "mcp__internal__wiki_query" in frontmatter
    assert "## Output Contract" in text


def test_llm_wiki_skill_is_discoverable():
    skills = prompt_assets.discover_skills(".claude/skills")
    names = {s.get("name") for s in skills}
    assert "llm-wiki-orchestration" in names


def test_live_capabilities_snapshot_mentions_llm_wiki_assets():
    snapshot = prompt_assets.build_live_capabilities_snapshot(str(Path.cwd()))
    assert "wiki-maintainer" in snapshot
    assert "llm-wiki-orchestration" in snapshot
