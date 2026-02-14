import re
from pathlib import Path


def _parse_frontmatter(md_text: str) -> str:
    m = re.match(r"^---\n(.*?)\n---\n", md_text, flags=re.S)
    assert m, "missing or malformed YAML frontmatter (expected starting/ending ---)"
    return m.group(1)


def _parse_tools(frontmatter: str) -> list[str]:
    for line in frontmatter.splitlines():
        if line.startswith("tools:"):
            raw = line.removeprefix("tools:").strip()
            return [t.strip() for t in raw.split(",") if t.strip()]
    raise AssertionError("frontmatter missing required 'tools:' line")


def test_image_expert_can_write_manifest_json():
    """
    Regression: image-expert is expected to write `work_products/media/manifest.json`.
    If it can't use `Write`, it will incorrectly try to use image tools for JSON outputs.
    """
    path = Path(".claude/agents/image-expert.md")
    text = path.read_text(encoding="utf-8")

    assert "manifest.json" in text, "test expects image-expert to advertise manifest.json support"

    frontmatter = _parse_frontmatter(text)
    tools = _parse_tools(frontmatter)
    assert "Write" in tools
    assert "Read" in tools  # used for outline-driven image planning and verification

