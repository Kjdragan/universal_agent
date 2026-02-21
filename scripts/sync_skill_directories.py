#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path


def _skill_map(root: Path) -> dict[str, Path]:
    skills: dict[str, Path] = {}
    if not root.exists():
        return skills
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if (child / "SKILL.md").exists() or (child / "skills.md").exists():
            skills[child.name] = child
    return skills


def _rel_symlink(link_path: Path, target_path: Path) -> None:
    rel_target = os.path.relpath(target_path, start=link_path.parent)
    link_path.symlink_to(rel_target)


def _ensure_mirror(name: str, src_root: Path, dst_root: Path) -> str:
    src = src_root / name
    dst = dst_root / name
    if dst.exists() or dst.is_symlink():
        return "exists"
    _rel_symlink(dst, src)
    return "created"


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    claude_root = (repo_root / ".claude" / "skills").resolve()
    agents_root = (repo_root / ".agents" / "skills").resolve()
    claude_root.mkdir(parents=True, exist_ok=True)
    agents_root.mkdir(parents=True, exist_ok=True)

    claude = _skill_map(claude_root)
    agents = _skill_map(agents_root)
    all_names = sorted(set(claude) | set(agents))

    created_claude = 0
    created_agents = 0
    for name in all_names:
        in_claude = name in claude
        in_agents = name in agents
        if in_claude and in_agents:
            continue
        if in_claude and not in_agents:
            result = _ensure_mirror(name, claude_root, agents_root)
            if result == "created":
                created_agents += 1
        elif in_agents and not in_claude:
            result = _ensure_mirror(name, agents_root, claude_root)
            if result == "created":
                created_claude += 1

    print(
        "skills sync complete:",
        f"created_in_.claude={created_claude}",
        f"created_in_.agents={created_agents}",
        f"total_skills={len(all_names)}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
