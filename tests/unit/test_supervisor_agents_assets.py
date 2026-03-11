from __future__ import annotations

import re
from pathlib import Path

from universal_agent import prompt_assets


FACTORY_AGENT_PATH = Path(".claude/agents/factory-supervisor.md")
CSI_AGENT_PATH = Path(".claude/agents/csi-supervisor.md")


def _frontmatter(text: str) -> str:
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S)
    assert match, "missing frontmatter"
    return match.group(1)


def test_factory_and_csi_supervisor_agents_exist_with_contract_sections():
    for path, expected_name in (
        (FACTORY_AGENT_PATH, "factory-supervisor"),
        (CSI_AGENT_PATH, "csi-supervisor"),
    ):
        text = path.read_text(encoding="utf-8")
        frontmatter = _frontmatter(text)
        assert f"name: {expected_name}" in frontmatter
        assert "tools: Read, Bash" in frontmatter
        assert "## Guardrails" in text
        assert "## Output Contract" in text
        assert "Advisory-only" in text or "advisory-only" in text


def test_live_capabilities_snapshot_mentions_supervisor_agents():
    snapshot = prompt_assets.build_live_capabilities_snapshot(str(Path.cwd()))
    assert "factory-supervisor" in snapshot
    assert "csi-supervisor" in snapshot
