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
    Scan skill directories recursively and parse SKILL.md/skills.md frontmatter.
    Now supports both PROJECT and USER level skills.
    Returns list of {name, description, path} for each skill.
    
    Prioritization: Project skills override User skills.
    Recursiveness: Scans entire tree for SKILL.md or skills.md files.
    """
    print(f"DEBUG: discover_skills called with dir={skills_dir}")
    import yaml

    # Define directories to scan
    dirs_to_scan = []
    
    # 1. Project Level (Highest Priority)
    project_skills = skills_dir or os.getenv("UA_SKILLS_DIR") or os.path.join(_PROJECT_ROOT, ".claude", "skills")
    dirs_to_scan.append(project_skills)
    
    # 2. User Level (Fallback)
    # Only if not strictly overridden by explicit arg
    if skills_dir is None:
        # Allow user to specify custom skills dir via env (e.g. base.quod)
        user_skills_env = os.getenv("UA_USER_SKILLS_DIR")
        user_skills = user_skills_env if user_skills_env else os.path.expanduser("~/.claude/skills")
        dirs_to_scan.append(user_skills)

    skills_map: dict[str, dict] = {} # normalized_name -> skill_dict
    overrides = _load_skill_overrides()
    
    # Valid marker filenames (case-insensitive checks)
    VALID_MARKERS = {"skill.md", "skills.md"}

    for d in dirs_to_scan:
        if not os.path.exists(d):
            continue

        for root, _, files in os.walk(d):
            # Find the first file that matches any of our markers
            skill_file = next((f for f in files if f.lower() in VALID_MARKERS), None)
            if not skill_file:
                continue
                
            skill_md = os.path.join(root, skill_file)
            skill_folder_name = os.path.basename(root)

            try:
                with open(skill_md, "r", encoding="utf-8") as f:
                    content = f.read()

                # Parse YAML frontmatter (between --- markers)
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        frontmatter = yaml.safe_load(parts[1])
                        if frontmatter and isinstance(frontmatter, dict):
                            name = frontmatter.get("name", skill_folder_name)
                            skill_key = _normalize_skill_key(name)
                            
                            # Skip if disabled by config
                            if overrides.get(skill_key) is False:
                                continue
                                
                            # Skip if we already found this skill in a higher-priority dir
                            if skill_key in skills_map:
                                continue

                            # GATING CHECK
                            is_avail, reason = _check_skill_requirements(frontmatter)
                            
                            skill_entry = {
                                "name": name,
                                "description": frontmatter.get("description", "No description"),
                                "path": skill_md,
                                "enabled": is_avail,
                                "frontmatter": frontmatter,  # Expose full metadata
                            }
                            
                            if not is_avail:
                                skill_entry["disabled_reason"] = reason
                                try:
                                    logfire.info("skill_gated", skill=name, reason=reason)
                                except Exception:
                                    pass
                            
                            skills_map[skill_key] = skill_entry

            except Exception as exc:
                # Skip malformed skill definition files
                try:
                    logfire.warning(
                        "skill_parse_error", file=skill_md, error=str(exc)
                    )
                except Exception:
                    pass
                continue
                    
    return list(skills_map.values())
                    
    return list(skills_map.values())


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


def _parse_agent_frontmatter(path: str) -> tuple[str, str]:
    """Return (name, description) parsed from an agent markdown file."""
    import yaml

    default_name = os.path.splitext(os.path.basename(path))[0]
    content = _load_file(path)
    if not content:
        return default_name, "Internal specialized agent."

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
                if isinstance(meta, dict):
                    name = str(meta.get("name") or default_name)
                    desc = str(meta.get("description") or "Internal specialized agent.")
                    return name, " ".join(desc.split())
            except Exception:
                pass

    return default_name, "Internal specialized agent."


def _discover_agent_profiles(project_root: str) -> list[dict]:
    """Discover specialist agents from project directories."""
    agents: list[dict] = []
    seen: set[str] = set()
    agent_dirs = [
        os.path.join(project_root, ".claude", "agents"),
        os.path.join(project_root, "src", "universal_agent", "agent_college"),
    ]

    for directory in agent_dirs:
        if not os.path.isdir(directory):
            continue
        for filename in sorted(os.listdir(directory)):
            if filename.startswith("_") or filename in {"common.py"}:
                continue

            full_path = os.path.join(directory, filename)
            if filename.endswith(".md"):
                name, description = _parse_agent_frontmatter(full_path)
            elif filename.endswith(".py"):
                name = os.path.splitext(filename)[0]
                description = "Internal specialized agent."
            else:
                continue

            key = _normalize_skill_key(name)
            if key in seen:
                continue
            seen.add(key)
            agents.append(
                {
                    "name": name,
                    "description": description,
                }
            )
    return agents


def _agent_domain(name: str) -> str:
    lowered = name.lower()
    if any(token in lowered for token in ("bowser", "playwright", "browserbase", "chrome")):
        return "🌐 Browser Operations"
    if any(token in lowered for token in ("research", "trend", "report", "data-analyst", "evaluation")):
        return "🔬 Research & Analysis"
    if any(token in lowered for token in ("image", "video", "mermaid", "youtube", "banana")):
        return "🎨 Creative & Media"
    if any(token in lowered for token in ("slack", "gmail", "calendar", "action", "ops", "system-configuration")):
        return "📣 Communication & Operations"
    if any(token in lowered for token in ("code", "task-decomposer", "integration", "runner", "critic", "config")):
        return "⚙️ Engineering & Automation"
    return "🛠 General"


def build_live_capabilities_snapshot(project_root: Optional[str] = None) -> str:
    """
    Build a runtime capabilities snapshot from discovered agents + skills.
    This prevents prompt drift when static capabilities.md gets stale.
    """
    from datetime import datetime

    root = project_root or _PROJECT_ROOT
    lines: list[str] = [
        "<!-- Runtime Capabilities Snapshot (Auto) -->",
        "",
        f"<!-- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} -->",
        "",
        "### Capability Routing Doctrine",
        "- Evaluate multiple capability lanes before selecting an execution path for non-trivial tasks.",
        "- Do not default to research/report unless explicitly requested or clearly required.",
        "- Browser tasks are Bowser-first: `claude-bowser-agent` (identity/session), `playwright-bowser-agent` (parallel/repeatable), `bowser-qa-agent` (UI validation).",
        "- Use `browserbase` when Bowser lanes are unavailable or cloud-browser behavior is explicitly needed.",
        "",
        "### 🤖 Specialist Agents (Live)",
    ]

    agents = _discover_agent_profiles(root)
    if agents:
        grouped: dict[str, list[dict]] = {}
        for agent in agents:
            grouped.setdefault(_agent_domain(agent["name"]), []).append(agent)

        for domain in sorted(grouped.keys()):
            lines.append(f"\n#### {domain}")
            for agent in sorted(grouped[domain], key=lambda item: item["name"].lower()):
                lines.append(f"- **{agent['name']}**: {agent['description']}")
                lines.append(f"  -> Delegate: `Task(subagent_type='{agent['name']}', ...)`")
    else:
        lines.append("- No specialist agents discovered.")

    lines.extend(
        [
            "",
            "### 📚 Skills (Live)",
        ]
    )

    skills = discover_skills(os.path.join(root, ".claude", "skills"))
    if skills:
        for skill in sorted(skills, key=lambda item: item.get("name", "").lower()):
            name = skill.get("name", "unknown")
            desc = " ".join(str(skill.get("description", "No description")).split())
            path = skill.get("path", "")
            lines.append(f"- **{name}**: {desc} (Source: `{path}`)")
    else:
        lines.append("- No skills discovered.")

    return "\n".join(lines)
