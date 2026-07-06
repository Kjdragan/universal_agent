"""Unit tests for the capability-shelf context helper (S3 eureka scoring).

Covers the two load-bearing invariants: a happy-path shelf renders skill name +
reuse count + description, and ANY failure (missing checkout) returns "" so the
judges run unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import universal_agent.services.demo_shelf_context as dsc


def _make_skill(skills_dir: Path, name: str, description: str) -> None:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: >-\n  {description}\n---\n\n# {name}\n\nbody\n",
        encoding="utf-8",
    )


def test_shelf_happy_path_renders_name_reuse_and_description(tmp_path: Path) -> None:
    dsc._CACHE.clear()
    skills = tmp_path / "skills"
    skills.mkdir()
    _make_skill(skills, "alpha-loop", "Does alpha things end to end")
    _make_skill(skills, "beta-render", "Renders a beta artifact")
    # Non-skill entries under skills/ must be ignored (not */SKILL.md).
    (skills / "README.md").write_text("readme", encoding="utf-8")
    (skills / "_reuse.json").write_text(
        json.dumps({"alpha-loop": 5, "beta-render": 1}), encoding="utf-8"
    )

    shelf = dsc.build_capability_shelf(root=tmp_path)

    assert "alpha-loop (reuse 5)" in shelf
    assert "beta-render (reuse 1)" in shelf
    assert "Does alpha things end to end" in shelf
    # Highest reuse first so "extend a high-reuse skill" candidates are salient.
    assert shelf.index("alpha-loop") < shelf.index("beta-render")
    # README is not a skill dir → excluded.
    assert "readme" not in shelf


def test_shelf_missing_reuse_json_defaults_counts_to_zero(tmp_path: Path) -> None:
    dsc._CACHE.clear()
    skills = tmp_path / "skills"
    skills.mkdir()
    _make_skill(skills, "gamma", "A gamma capability")
    # No _reuse.json at all — must not raise; count defaults to 0.
    shelf = dsc.build_capability_shelf(root=tmp_path)
    assert "gamma (reuse 0)" in shelf


def test_shelf_fail_safe_returns_empty_when_dir_missing(tmp_path: Path) -> None:
    dsc._CACHE.clear()
    assert dsc.build_capability_shelf(root=tmp_path / "does-not-exist") == ""


def test_shelf_fail_safe_returns_empty_when_no_skills(tmp_path: Path) -> None:
    dsc._CACHE.clear()
    (tmp_path / "skills").mkdir()  # empty skills dir
    assert dsc.build_capability_shelf(root=tmp_path) == ""


def test_block_present_carries_eureka_instruction(tmp_path: Path) -> None:
    dsc._CACHE.clear()
    skills = tmp_path / "skills"
    skills.mkdir()
    _make_skill(skills, "alpha-loop", "Does alpha things")
    (skills / "_reuse.json").write_text(json.dumps({"alpha-loop": 3}), encoding="utf-8")

    block = dsc.capability_shelf_block(root=tmp_path)

    assert "CAPABILITY SHELF" in block
    assert "landmark" in block.lower()
    assert "me-too" in block.lower()
    assert "alpha-loop (reuse 3)" in block


def test_block_empty_when_shelf_unavailable(tmp_path: Path) -> None:
    dsc._CACHE.clear()
    # An empty/absent shelf must yield "" so `system += block` is byte-identical.
    assert dsc.capability_shelf_block(root=tmp_path / "does-not-exist") == ""


def test_shelf_is_process_cached(tmp_path: Path) -> None:
    dsc._CACHE.clear()
    skills = tmp_path / "skills"
    skills.mkdir()
    _make_skill(skills, "alpha", "one")
    first = dsc.build_capability_shelf(root=tmp_path)
    # Mutating disk after the first read must NOT change the cached value.
    _make_skill(skills, "beta", "two")
    second = dsc.build_capability_shelf(root=tmp_path)
    assert first == second
    assert "beta" not in second
