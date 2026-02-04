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


import shutil

def _check_skill_requirements(frontmatter: dict) -> tuple[bool, str]:
    """
    Check if skill requirements are met.
    Returns (is_available, reason).
    """
    try:
        # Check explicit binary requirements
        # metadata: { clawdbot: { requires: { bins: ["gh"] } } }
        clawdbot_meta = frontmatter.get("metadata", {}).get("clawdbot", {})
        requires = clawdbot_meta.get("requires", {})
        
        # Check mandatory binaries
        bins = requires.get("bins", [])
        for binary in bins:
            if not shutil.which(binary):
                return False, f"Missing binary: {binary}"

        # Check 'anyBins' (at least one must exist)
        any_bins = requires.get("anyBins", [])
        if any_bins:
            if not any(shutil.which(b) for b in any_bins):
                return False, f"Missing any of: {any_bins}"

        return True, ""
    except Exception as e:
        return True, ""  # Default to allowing on error to avoid blocking valid skills


def _normalize_skill_key(value: str) -> str:
    return value.strip().lower()


def _load_skill_overrides() -> dict[str, bool]:
    overrides: dict[str, bool] = {}
    env_disabled = os.getenv("UA_SKILLS_DISABLED", "")
    if env_disabled:
        for raw in env_disabled.split(","):
            key = _normalize_skill_key(raw)
            if key:
                overrides[key] = False
    try:
        from universal_agent.ops_config import load_ops_config

        cfg = load_ops_config()
        entries = cfg.get("skills", {}).get("entries", {})
        if isinstance(entries, dict):
            for key, payload in entries.items():
                norm = _normalize_skill_key(str(key))
                enabled = None
                if isinstance(payload, dict):
                    enabled = payload.get("enabled")
                elif isinstance(payload, bool):
                    enabled = payload
                if isinstance(enabled, bool):
                    overrides[norm] = enabled
    except Exception:
        pass
    return overrides


def discover_skills(skills_dir: Optional[str] = None) -> list[dict]:
    """
    Scan .claude/skills/ directory and parse SKILL.md frontmatter.
    Returns list of {name, description, path} for each skill.
    
    NOW IMPLEMENTS GATING: Hides skills if dependencies are missing.
    """
    import yaml

    if skills_dir is None:
        skills_dir = os.getenv("UA_SKILLS_DIR") or os.path.join(_PROJECT_ROOT, ".claude", "skills")

    skills: list[dict] = []
    overrides = _load_skill_overrides()

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
                            skill_key = _normalize_skill_key(frontmatter.get("name", skill_name))
                            if overrides.get(skill_key) is False:
                                continue
                            # GATING CHECK
                            is_avail, reason = _check_skill_requirements(frontmatter)
                            if is_avail:
                                skills.append(
                                    {
                                        "name": frontmatter.get("name", skill_name),
                                        "description": frontmatter.get(
                                            "description", "No description"
                                        ),
                                        "path": skill_md,
                                    }
                                )
                            else:
                                try:
                                    logfire.info("skill_gated", skill=skill_name, reason=reason)
                                except Exception:
                                    pass

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
    Generate Markdown list of available skills for system prompt injection.
    Using Markdown check-list style to encourage reading.
    """
    if not skills:
        return ""

    lines = [
        "## 📚 AVAILABLE SKILLS (Standard Operating Procedures)",
        "You MUST read the relevant SOP before executing these tasks:",
    ]
    for skill in skills:
        lines.append(
            f"- **{skill['name']}**: {skill['description']} (Path: `{skill['path']}`)"
        )
    return "\n".join(lines)
