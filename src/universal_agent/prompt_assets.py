"""
Prompt assets used for Claude system prompt construction.

This module centralizes knowledge base loading and skill discovery so the
CLI and API paths behave consistently.
"""

from __future__ import annotations

import os
from typing import Optional

import logfire


_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

_TOOL_KNOWLEDGE_CONTENT: Optional[str] = None
_TOOL_KNOWLEDGE_BLOCK: Optional[str] = None


def load_knowledge(project_root: Optional[str] = None) -> str:
    """
    Load all knowledge files from .claude/knowledge/ directory.
    """
    root = project_root or _PROJECT_ROOT
    knowledge_dir = os.path.join(root, ".claude", "knowledge")

    if not os.path.exists(knowledge_dir):
        return ""

    knowledge_parts = []
    for filename in sorted(os.listdir(knowledge_dir)):
        if filename.endswith(".md"):
            filepath = os.path.join(knowledge_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    knowledge_parts.append(f.read())
            except Exception:
                pass  # Skip files that can't be read

    return "\n\n---\n\n".join(knowledge_parts) if knowledge_parts else ""


def get_tool_knowledge_content() -> str:
    global _TOOL_KNOWLEDGE_CONTENT
    if _TOOL_KNOWLEDGE_CONTENT is None:
        _TOOL_KNOWLEDGE_CONTENT = load_knowledge()
    return _TOOL_KNOWLEDGE_CONTENT or ""


def get_tool_knowledge_block() -> str:
    global _TOOL_KNOWLEDGE_BLOCK
    if _TOOL_KNOWLEDGE_BLOCK is None:
        content = get_tool_knowledge_content()
        _TOOL_KNOWLEDGE_BLOCK = f"## Tool Knowledge\n{content}" if content else ""
    return _TOOL_KNOWLEDGE_BLOCK or ""


def discover_skills(skills_dir: Optional[str] = None) -> list[dict]:
    """
    Scan .claude/skills/ directory and parse SKILL.md frontmatter.
    Returns list of {name, description, path} for each skill.

    Progressive disclosure: We only load name+description here.
    Full SKILL.md content is loaded by the agent when needed via read_local_file.
    """
    import yaml

    if skills_dir is None:
        skills_dir = os.path.join(_PROJECT_ROOT, ".claude", "skills")

    skills: list[dict] = []

    if not os.path.exists(skills_dir):
        return skills

    for skill_name in os.listdir(skills_dir):
        skill_path = os.path.join(skills_dir, skill_name)
        skill_md = os.path.join(skill_path, "SKILL.md")

        if os.path.isdir(skill_path) and os.path.exists(skill_md):
            try:
                with open(skill_md, "r", encoding="utf-8") as f:
                    content = f.read()

                # Parse YAML frontmatter (between --- markers)
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        frontmatter = yaml.safe_load(parts[1])
                        if frontmatter and isinstance(frontmatter, dict):
                            skills.append(
                                {
                                    "name": frontmatter.get("name", skill_name),
                                    "description": frontmatter.get(
                                        "description", "No description"
                                    ),
                                    "path": skill_md,
                                }
                            )
            except Exception as exc:
                # Skip malformed SKILL.md files
                try:
                    logfire.warning(
                        "skill_parse_error", skill=skill_name, error=str(exc)
                    )
                except Exception:
                    pass
                continue

    return skills


def generate_skills_xml(skills: list[dict]) -> str:
    """
    Generate <available_skills> XML block for system prompt injection.
    This enables Claude to be aware of skills and read them when relevant.
    """
    if not skills:
        return ""

    lines = ["<available_skills>"]
    for skill in skills:
        lines.append(
            f"""<skill>
  <name>{skill['name']}</name>
  <description>{skill['description']}</description>
  <path>{skill['path']}</path>
</skill>"""
        )
    lines.append("</available_skills>")
    return "\n".join(lines)
