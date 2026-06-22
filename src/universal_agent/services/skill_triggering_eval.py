"""Evaluate whether a skill *triggers* — via the canonical Claude Agent SDK path,
which works across models (Anthropic AND ZAI/GLM), unlike the legacy
``claude -p`` + ``.claude/commands/`` command-file probe.

Verified 2026-06-22: glm-5.2 and claude-opus-4-8 both invoke a real
``.claude/skills/`` skill via ``query(skills="all", setting_sources=[...])``. The
old command-file harness under-tests skills and false-reads 0 on GLM — see
``project_docs/07_tools/03_skills_system.md`` § Gotchas.

Reusable beyond the description optimizer: anything that needs "does skill X fire
for prompt P on model M?" can call ``evaluate_triggering``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any


def _rewrite_description(skill_md: str, new_description: str | None) -> str:
    """Return SKILL.md text with its frontmatter ``description:`` replaced.

    ponytail: handles the common single-line ``description:`` (what candidate
    descriptions are). If ``new_description`` is None, the file is returned
    unchanged (test the skill's own shipped description).
    """
    if new_description is None:
        return skill_md
    # Collapse to one physical line; the candidate is a single string anyway.
    one_line = " ".join(new_description.split())
    return re.sub(
        r"(?m)^description:.*$",
        f"description: {one_line}",
        skill_md,
        count=1,
    )


def _skill_name_from_md(skill_md: str, fallback: str) -> str:
    m = re.search(r"(?m)^name:\s*(.+)$", skill_md)
    return m.group(1).strip() if m else fallback


async def _invoked_once(
    *, project_dir: Path, prompt: str, model: str, env: dict[str, str], timeout_s: int
) -> bool:
    """One Agent-SDK turn-set; True iff the model invoked the Skill tool."""
    from claude_agent_sdk import ClaudeAgentOptions, query

    opts = ClaudeAgentOptions(
        cwd=str(project_dir),
        setting_sources=["project"],     # discover the temp .claude/skills/ skill
        skills="all",                    # enable Skill tool + all skills
        model=model,
        allowed_tools=["Skill", "Read", "Glob", "Grep"],  # NOT Bash: can't actually act
        permission_mode="bypassPermissions",
        max_turns=2,
        env=env,
    )
    # Drive the SDK generator explicitly and aclose() it in finally. Returning from
    # *inside* the `async for` (or cancelling it via wait_for) leaves the generator's
    # internal task running and the GC then prints
    # "aclose(): asynchronous generator is already running". Break out, then close it
    # ourselves under a clean asyncio.timeout boundary instead.
    agen = query(prompt=prompt, options=opts)
    found = False
    try:
        async with asyncio.timeout(timeout_s):
            async for msg in agen:
                if any(
                    getattr(block, "name", None) == "Skill"
                    for block in (getattr(msg, "content", None) or [])
                ):
                    found = True
                    break
    except Exception:
        # Timeout or harness error == "did not trigger" (e.g. glm-5.2 too slow).
        pass
    finally:
        try:
            await agen.aclose()
        except Exception:
            pass
    return found


async def evaluate_triggering(
    *,
    skill_path: str | Path,
    prompts: list[dict[str, Any]],
    model: str,
    env: dict[str, str],
    description: str | None = None,
    runs_per_query: int = 1,
    timeout_s: int = 300,
) -> dict[str, Any]:
    """Score how well a skill triggers.

    ``prompts`` = ``[{"query": str, "should_trigger": bool}, ...]``. ``description``
    overrides the skill's shipped description for the test (used to score a
    *candidate*); None tests the shipped one. Returns per-query trigger rates +
    pass/fail (pass = (rate>=0.5) == should_trigger) and a summary.
    """
    skill_path = Path(skill_path)
    src_md = (skill_path / "SKILL.md").read_text(encoding="utf-8")
    name = _skill_name_from_md(src_md, skill_path.name)
    md = _rewrite_description(src_md, description)

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="skilltrig_") as tmp:
        proj = Path(tmp)
        dest = proj / ".claude" / "skills" / name
        shutil.copytree(skill_path, dest)
        (dest / "SKILL.md").write_text(md, encoding="utf-8")

        for item in prompts:
            triggers = 0
            for _ in range(max(1, runs_per_query)):
                if await _invoked_once(
                    project_dir=proj, prompt=item["query"], model=model,
                    env=env, timeout_s=timeout_s,
                ):
                    triggers += 1
            rate = triggers / max(1, runs_per_query)
            passed = (rate >= 0.5) == bool(item["should_trigger"])
            results.append({
                "query": item["query"],
                "should_trigger": bool(item["should_trigger"]),
                "trigger_rate": rate,
                "pass": passed,
            })

    passed_n = sum(1 for r in results if r["pass"])
    return {
        "skill": name,
        "model": model,
        "results": results,
        "passed": passed_n,
        "total": len(results),
    }


def _demo() -> None:
    """Runnable self-check for the pure logic (no live model call)."""
    md = "---\nname: foo\ndescription: old desc here\n---\n# Foo\nbody\n"
    out = _rewrite_description(md, "brand new\ndescription text")
    assert "description: brand new description text" in out, out
    assert "old desc here" not in out
    assert _skill_name_from_md(md, "fallback") == "foo"
    assert _rewrite_description(md, None) == md  # None = unchanged
    print("skill_triggering_eval self-check OK")


if __name__ == "__main__":
    _demo()
