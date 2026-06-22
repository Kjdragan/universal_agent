#!/usr/bin/env python3
"""Skill-description optimizer — UA-owned driver with an auth-mode parameter.

Why this exists (not the bundled skill-creator optimizer): the bundled
``run_loop.py`` (a) constructs the raw ``anthropic.Anthropic()`` SDK, which can't
use the Max subscription (OAuth), and (b) evals triggering via a ``claude -p`` +
``.claude/commands/`` command-file probe that false-reads 0 on non-Anthropic
models. This driver fixes both by composing two reusable services:

  * ``services.inference_auth.build_inference_env`` — pick the auth path.
  * ``services.skill_triggering_eval.evaluate_triggering`` — Agent-SDK skill
    triggering that works on Anthropic AND ZAI/GLM.

The improve step rides the subscription too (``claude -p``), never the raw SDK.

  --auth-mode anthropic  (DEFAULT) → Max OAuth, real Opus. No API key.
  --auth-mode zai                  → ZAI routing, opus-tier GLM (glm-5.2).
  --auth-mode auto                 → anthropic if a subscription cred exists, else zai.

Usage:
  python -m scripts.skill_desc_optimizer --skill-path <dir> --eval-set <json> \
      [--auth-mode anthropic|zai|auto] [--max-iterations N] [--report out.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from universal_agent.services.inference_auth import build_inference_env  # noqa: E402
from universal_agent.services.skill_triggering_eval import (
    evaluate_triggering,  # noqa: E402
)

_IMPROVE_PROMPT = """\
You are improving the `description` field of a Claude Code SKILL.md so the model \
reliably invokes the skill for the right requests and NOT for the wrong ones.

SKILL.md (for context):
{skill_md}

Current description:
{current}

These eval queries were classified WRONG with the current description \
(should_trigger=true means the skill should have fired):
{failures}

Write a better description: trigger-rich for the true cases, with clear NOT-for \
clauses for near-misses. Keep it under 1024 characters. Respond with ONLY the new \
description inside <new_description>...</new_description> tags."""


def _improve_via_cli(skill_md: str, current: str, failures: list[dict], model: str,
                     env: dict[str, str], timeout_s: int = 300) -> str:
    """Propose a better description via `claude -p` (rides subscription/ZAI; no raw SDK)."""
    fail_lines = "\n".join(
        f"  - should_trigger={f['should_trigger']} got_rate={f['trigger_rate']}: {f['query']}"
        for f in failures
    ) or "  (none)"
    prompt = _IMPROVE_PROMPT.format(skill_md=skill_md[:4000], current=current, failures=fail_lines)
    cmd = ["claude", "-p", prompt, "--output-format", "text", "--model", model]
    out = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout_s)
    text = out.stdout or ""
    m = re.search(r"<new_description>(.*?)</new_description>", text, re.DOTALL)
    return (m.group(1).strip() if m else text.strip()).strip('"') or current


async def _run(args: argparse.Namespace) -> dict:
    env, model, resolved_mode = build_inference_env(args.auth_mode)
    print(f"auth-mode: {args.auth_mode} -> resolved={resolved_mode}  model={model}", flush=True)

    eval_set = json.loads(Path(args.eval_set).read_text())
    skill_md = (Path(args.skill_path) / "SKILL.md").read_text(encoding="utf-8")
    cur_desc_match = re.search(r"(?m)^description:\s*(.+)$", skill_md)
    current = cur_desc_match.group(1).strip() if cur_desc_match else ""

    n_test = max(1, int(len(eval_set) * args.holdout))
    test_set, train_set = eval_set[:n_test], eval_set[n_test:] or eval_set

    history = []
    for iteration in range(1, args.max_iterations + 1):
        report = await evaluate_triggering(
            skill_path=args.skill_path, prompts=train_set + test_set, model=model, env=env,
            description=current, runs_per_query=args.runs_per_query, timeout_s=args.timeout,
        )
        train_q = {q["query"] for q in train_set}
        train_res = [r for r in report["results"] if r["query"] in train_q]
        test_res = [r for r in report["results"] if r["query"] not in train_q]
        train_pass = sum(1 for r in train_res if r["pass"])
        test_pass = sum(1 for r in test_res if r["pass"])
        print(f"  iter {iteration}: train {train_pass}/{len(train_res)}  "
              f"test {test_pass}/{len(test_res)}", flush=True)
        history.append({"iteration": iteration, "description": current,
                        "train_pass": train_pass, "train_total": len(train_res),
                        "test_pass": test_pass, "test_total": len(test_res)})
        if train_pass == len(train_res) or iteration == args.max_iterations:
            break
        failures = [r for r in train_res if not r["pass"]]
        current = _improve_via_cli(skill_md, current, failures, model, env, args.timeout)
        print(f"  proposed: {current[:120]}...", flush=True)

    best = max(history, key=lambda h: (h["test_pass"], h["train_pass"]))
    result = {"auth_mode": args.auth_mode, "resolved_mode": resolved_mode, "model": model,
              "best_description": best["description"], "best_test": f"{best['test_pass']}/{best['test_total']}",
              "history": history}
    if args.report:
        Path(args.report).write_text(json.dumps(result, indent=2))
    print(f"\nBest test score: {result['best_test']}")
    print(f"Best description:\n{best['description']}")
    return result


def main() -> int:
    p = argparse.ArgumentParser(description="Skill-description optimizer (auth-mode aware)")
    p.add_argument("--skill-path", required=True, help="Path to the skill dir (contains SKILL.md)")
    p.add_argument("--eval-set", required=True, help="JSON list of {query, should_trigger}")
    p.add_argument("--auth-mode", choices=["anthropic", "zai", "auto"], default="anthropic")
    p.add_argument("--max-iterations", type=int, default=3)
    p.add_argument("--runs-per-query", type=int, default=1)
    p.add_argument("--holdout", type=float, default=0.4)
    p.add_argument("--timeout", type=int, default=300, help="Per-call timeout seconds")
    p.add_argument("--report", default=None)
    args = p.parse_args()
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
