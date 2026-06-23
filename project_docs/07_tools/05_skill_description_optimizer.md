---
title: Skill-Description Optimizer (and why description-tuning has a ceiling)
status: active
canonical: true
subsystem: skill-desc-optimizer
code_paths:
  - src/universal_agent/services/inference_auth.py
  - src/universal_agent/services/skill_triggering_eval.py
  - scripts/skill_desc_optimizer.py
  - .claude/skills/skill-creator/
last_verified: 2026-06-22
---

# Skill-Description Optimizer (and why description-tuning has a ceiling)

> **TL;DR for a future investigator (human or LLM): this is a *weak vein to mine*.**
> The optimizer is a **functional, usable solution** and our skill descriptions are
> already passable. We exhaustively tried to *improve* skill-triggering accuracy by
> tuning descriptions (automated rewrites *and* sharp hand-written exclusion clauses)
> and it did **not** help — on the one real defect we found it either did nothing or
> made it **worse**. **Do not re-open "tune the description to fix triggering" expecting
> incremental gains.** The tool's payoff is at *scale* (many skills, real eval sets),
> not on already-strong single descriptions. Full evidence below so you can reproduce
> rather than re-derive.

## 1. What this is (the shipped subsystem)

A UA-owned skill-description optimizer + two reusable services. It does **not** patch
Anthropic's bundled `skill-creator` skill — it composes proven primitives instead.

| File | Role |
|---|---|
| `scripts/skill_desc_optimizer.py` | Driver. `--auth-mode {anthropic,zai,auto}` (default `anthropic`). Eval → improve → re-eval loop; keeps the best description by held-out test score. |
| `src/universal_agent/services/inference_auth.py::build_inference_env` | Pick the auth path for **any** out-of-session inference: `anthropic` (scrub `ANTHROPIC_*` → Max OAuth, real Opus), `zai` (inject ZAI routing, pop `ANTHROPIC_API_KEY`, opus-tier glm-5.2), `auto`. Reusable. |
| `src/universal_agent/services/skill_triggering_eval.py::evaluate_triggering` | "Does this skill trigger for prompt P on model M?" via the canonical **Agent SDK** path (`query(skills="all", setting_sources=[...])` against a REAL `.claude/skills/` skill). Works on Anthropic AND ZAI/GLM. Reusable. |

Run:
```bash
python scripts/skill_desc_optimizer.py --skill-path <dir> --eval-set <json> \
    [--auth-mode anthropic|zai|auto] [--max-iterations N] [--runs-per-query K]
# eval-set = [{"query": "...", "should_trigger": true|false}, ...]
```

The `improve` step rides `claude -p` (subscription or ZAI), never the raw `anthropic`
SDK — see § "Why the bundled optimizer broke."

## 2. Why we built it — the bundled `skill-creator` optimizer broke

The investigation started because Anthropic's bundled `skill-creator` description
optimizer (`run_loop.py`) crashed in an interactive Max session. Two stale-copy bugs,
**neither of which is the Claude Agent SDK or a library version**:

1. **Auth.** The vendored `run_loop.py`/`improve_description.py` construct the raw
   `anthropic.Anthropic()` SDK, which needs an `ANTHROPIC_API_KEY`/`AUTH_TOKEN`. A Max
   OAuth session exposes none → "Could not resolve authentication method." Anthropic
   **already fixed this upstream** by switching to `claude -p` (rides OAuth, no key); the
   fixed copy is on the desktop at `~/.claude/plugins/marketplaces/anthropic-agent-skills/skills/skill-creator/`.
2. **Eval harness.** `run_eval.py` probes triggering by writing a `.claude/commands/`
   **command** file (not a skill) and watching `claude -p` for a `Skill` tool_use. That
   under-tests skills and **false-read 0% on GLM** — which led to an initial WRONG
   conclusion that "GLM is skill-blind."

Background auth facts captured in
[`../06_platform/05_environments.md`](../06_platform/05_environments.md) §"Running
inference from a script"; the GLM-triggering correction in
[`03_skills_system.md`](03_skills_system.md) §Gotchas.

## 3. What we tried, in order (the investigation log)

1. **Repro the bundled-optimizer auth crash** → confirmed raw-SDK + no-OAuth-key is the cause.
2. **Route the bundled optimizer through ZAI** (inject ZAI creds, glm-5.2) → the improve
   step ran, but `run_eval`'s command-file harness returned **0% recall** on glm-5.2.
3. **Decisive A/B — same harness, Opus vs GLM** → Opus triggered, GLM 0%. Initially read
   as "GLM skill-blind."
4. **Test the *canonical* path (Agent SDK, real skill, `skills="all"`)** → **both glm-5.2
   AND Opus invoked the skill** (`Skill(skill='provision-local-gpu-ollama')`). Corrected
   the verdict: the harness was wrong, not GLM.
5. **Build the UA optimizer** (the subsystem above) on the Agent-SDK eval + auth-mode env.
   Live-verified both modes.
6. **Head-to-head, same 6-query set, 1 then 3 runs/query** → glm-5.2 **matched** Opus on
   every clear case (3/3 triggers, 0/3 non-triggers) and was **more precise** on one
   near-miss (Opus 5/6, glm-5.2 6/6). Opus's one miss: it over-triggers the local-Ollama
   skill on *"switch this agent to the anthropic opus model instead of glm."* The 3×-run
   confirmed it's **consistent** (not noise).
7. **Can the optimizer fix that over-trigger?** Ran it (anthropic, 3 iterations). The
   improve step produced two rewrites; **neither fixed it** — train stuck at 3/4; it kept
   the original description.
8. **Can a *sharp human clause* fix it?** Added an explicit exclusion that named the
   failing case ("EXPLICITLY NOT for switching which hosted model an agent uses, e.g. 'use
   Opus instead of GLM'…"). Re-eval on Opus, 3×: the over-trigger went **0.67 → 1.00 — it
   got WORSE.** Real triggers unaffected.

## 4. How to reproduce

- **Eval set** (`zai_opt_eval.json`-style): 3 should-trigger ("install ollama…", "set up a
  local model at localhost:11434…", "offline llm on the gpu…") + 3 should-not ("switch the
  agent to the opus model instead of glm", "the ollama container on the vps is pegging
  cpu", "install the cuda toolkit / fix torch"). Skill under test: `provision-local-gpu-ollama`.
- **Compare models:** call `evaluate_triggering(skill_path=..., prompts=..., model=..., env=...)`
  for `build_inference_env("anthropic")` and `("zai")`, `runs_per_query=3`, sequential.
- **Expected:** Opus 5/6 (over-triggers the "switch model" near-miss ~0.67–1.0); glm-5.2 6/6.
- **Reproduce the ceiling:** append a NOT-for clause that names "Opus/GLM/switch/model" and
  re-eval on Opus → the over-trigger does **not** drop (observed: it rose to 1.0).
- Keep ZAI runs **sequential** (the ZAI coding plan is concurrency-sensitive; cost is not a
  concern, concurrency is).

## 5. Conclusions

1. **The process is functional and the original descriptions are passable — keep them.**
   Every clear case triggers correctly on both models; the lone defect is a low-stakes
   near-miss false-positive on Opus (the skill no-ops if wrongly invoked).
2. **Description-based triggering has a ceiling.** Some model mis-associations cannot be
   worded away. Automated *and* sharp human exclusion clauses both failed on the one defect
   we found.
3. **Naming the excluded case in a NOT-for clause can backfire** (a *pink-elephant* effect):
   the excluded keywords ("Opus", "GLM", "switch", "model") raise the salience that links the
   skill to the very query you want to exclude. Observed the over-trigger get *worse*. Treat
   "add a NOT-for clause" as **not guaranteed to help**.
   > Caveat — this is one skill, one near-miss, on Opus, with real run-to-run variance.
   > Treat (2) and (3) as strong tentative patterns, not laws.
4. **The tool's real payoff is at scale**, not on single already-strong descriptions: run it
   across the whole skill library with proper, larger eval sets to find *unknown* boundary
   problems where tuning genuinely helps. That is a separate future initiative.
5. **GLM is a viable eval/triggering model** (matched Opus here) — but it's slower per call,
   so for the eval half Opus is the practical default; `--auth-mode zai` is the working
   fallback.
