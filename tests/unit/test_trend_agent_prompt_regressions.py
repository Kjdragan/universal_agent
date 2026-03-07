from __future__ import annotations

from pathlib import Path


AGENTS_DIR = Path(".claude/agents")


def test_no_last30days_reference_in_agent_prompts():
    content = "\n".join(path.read_text(encoding="utf-8") for path in AGENTS_DIR.glob("*.md"))
    assert "last30days" not in content


def test_csi_trend_analyst_prompt_exists_and_is_csi_first():
    prompt_path = AGENTS_DIR / "csi-trend-analyst.md"
    content = prompt_path.read_text(encoding="utf-8")
    assert "CSI-first" in content
    assert "Mission context informs prioritization; it does not replace CSI-first analysis." in content
