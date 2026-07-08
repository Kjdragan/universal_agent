# Handoff — the original `/dragan:demo` run (n8n image → Claude Agent SDK) + its skill

**For the next session.** Goal: pick up the **original `/dragan:demo` build** that turned a screenshot
of an n8n *Google Trends → Google Sheets* workflow into a **Claude Agent SDK process**, understand what
happened in that run, and keep working on the **skill it produced** (`analyze-google-trends`, since
recrafted into a two-signal "trend pulse"). Written 2026-06-27 (desktop, Opus 4.8 / Max).

> The prior session is hard to access — this doc + the referenced artifacts are how you continue. Nothing
> here needs redoing; it's all shipped. The point is to **investigate the run and evolve the skill**.

---

## 1. What the run was (one paragraph)

`/dragan:demo` was pointed at an **image** of an n8n Trends→Sheets automation with the steer *"Dont use
n8n, use this by creating an claude agent sdk process."* So the deliverable was **the example workflow
rebuilt as a Claude Agent SDK program** (not an n8n converter — that was an early misread, corrected).
The factory scaffolded a standalone repo, researched the Agent SDK + Google Trends surfaces, built a
real SDK agent, gated it, and extracted a reusable skill. It **passed against the real Anthropic
endpoint** (`manifest.json`: `acceptance_passed=True, endpoint_hit=anthropic_native, archetype=agent_sdk,
demo_id=trends-to-sheets-agent`).

## 2. Where everything lives (investigate here — don't reconstruct from memory)

- **The demo repo (source of truth):** `~/lrepos/demo-trends-to-sheets-agent/` ·
  GitHub `git@github.com:Kjdragan/demo-trends-to-sheets-agent.git`.
  - `agent.py` — the Claude Agent SDK process (the heart of "what the run built").
  - `trends_pipeline.py` — pure fetch/parse/filter/write functions · `test_pipeline.py`.
  - `BRIEF.md` / `ACCEPTANCE.md` / `CONCEPT.md` — the conductor's brief + gate + operator briefing.
  - `SOURCES/agent_sdk_surface.md`, `SOURCES/trends_and_sheets.md` — the research grounding the build cited.
  - `manifest.json`, `goal_condition.txt`, `config.json`, `output/`.
  - `skill/` — the produced skill (see §3).
  - ⚠️ **Branch note:** the recraft work is on branch **`robust-auto-merge`**, NOT `main`. Reconcile
    this early (check whether `main` is behind and whether it should be merged/retargeted).
- **The build story (the run itself):**
  - **Git history is the timeline** — `git -C ~/lrepos/demo-trends-to-sheets-agent log --oneline`:
    `3e731a4` scaffold → `9b72a6f` "Google Trends → Sheets as a Claude Agent SDK process" (the build) →
    `e9fcc49` skill reframe → `3c2c596` recraft → `8e7af21` windows.
  - **This session's transcript** (richest record of the iterative debugging):
    `~/.claude/projects/-home-kjdragan-lrepos-universal-agent/6ef13267-b2d3-4c97-9ebe-83251fd492ba.jsonl`
    (the compaction summary at the top of that session digests the ~8-iteration recursion-bug hunt).
  - **Factory lessons from this run:** `~/lrepos/demo_factory/lessons/DEMO_BUILD_LESSONS.md` (2026-06-26
    entries: recursion fork-bomb, ZAI/Anthropic auth 401, gate-too-weak → DEMO_SELFCHECK).
  - **Factory vault entry:** `~/lrepos/demo_factory/vault/entries/google-trends-google-sheets-as-a-claude-agent-sdk-process.md`.
- **Operator briefings (scratchpad, rendered):**
  - Original concept: `~/lrepos/universal_agent/scratch_archive/2026-06-26/104231__trends-to-sheets-agent-concept__CONCEPT.md`
  - Recraft design: https://uaonvps.taildcc090.ts.net/scratch/trend-pulse-design/trend_research_design.html

## 3. The skill — its evolution and current state

The skill is the durable artifact. It went through three stages (don't redo; reference the commits):
1. **`n8n-to-agent-sdk`** — a how-to guide, *miscast* (named like a converter). Retired (dragan-plugins #18).
2. **`analyze-google-trends`** — reframed to a real, parameterized Google-Trends-board analysis skill.
3. **Two-signal "trend pulse" (current, LIVE)** — fuses the Google Trends board (*what people search*)
   with a keyless HN+Reddit conversation pulse (*what people say*), prompt-driven modes
   (PULSE/DEEP/COMPARE/MONITOR), `--window day|week|month`, last30days-style packaging.
   - **Source:** `~/lrepos/demo-trends-to-sheets-agent/skill/` — `SKILL.md`,
     `scripts/fetch_trends.py` (board), `scripts/research_topic.py` (HN Algolia + Reddit, deterministic
     recency×engagement scoring, `--selftest`).
   - **Live:** `/dragan:analyze-google-trends` via dragan-plugins PRs **#30** (recraft) + **#31**
     (windows), both merged. Factory-library copy: demo_factory **#55** (merged).
   - **Verified:** both scripts' `--selftest` pass offline; live runs confirmed across day/week/month.
   - Design rationale (don't re-derive): the recraft design exhibit URL in §2.

## 4. Open threads to continue

1. **Continue evolving the skill** — obvious next moves: a rename to `trend-pulse` (the name
   `analyze-google-trends` undersells it; would retire the old `/dragan:` command and re-promote);
   richer COMPARE output; a rendered scratchpad-HTML report mode for DEEP.
2. **The X lane (separate active thread — see the companion handoff).** Decision reached: add an optional
   X signal by **reusing the self-healing vendored `bird-search` (cookie auth → X GraphQL `SearchTimeline`)
   from the `last30days` skill** rather than building our own (its `runtime-query-ids.js` re-discovers
   X's rotating query IDs automatically). Prereqs: **Node ≥22** (box has v20.12.2 — bump needed) and
   **manual `auth_token`/`ct0` cookies** (auto-extract needs an uninstalled pkg). Risks: X ToS, the
   cookie is a full account credential — desktop-only, never VPS/autonomous, gitignored, never echoed.
   Full detail: **`/tmp/handoff_x_access_investigation.md`**.
3. **The autonomous/MONITOR form already exists** — `agent.py` IS the scheduled "fetch→analyze→Sheet"
   version. To productionize: a `deployment/systemd/` timer on the VPS.

## 5. The hard-won gotchas from the run (so you don't re-hit them)

Captured fully in `demo_factory/lessons/DEMO_BUILD_LESSONS.md` + the demo's `BRIEF.md` "Known traps" —
reference those. In one line each: **recursion fork-bomb** (nested `claude` re-ran the entry script via a
Stop hook → output inflation; fix: `setting_sources=[]` + `cwd=tempfile.mkdtemp()` + a `_CHILD` env guard
+ `tools=[]`); **decoupled write** (host commits the sink once, never from a model-called tool);
**Anthropic-vs-ZAI auth** (strip all `ANTHROPIC_*` when a Max login exists, else 401); **Google Trends
RSS is geo-only** (`pytrends` archived; granular Trends needs a paid API).

## 6. Suggested skills (invoke these)
- **`claude-api`** and/or the **`claude-code-guide`** agent / **`agent-sdk-dev`** — to (re)verify the
  current Claude Agent SDK surface before touching `agent.py` (read docs, don't recall).
- **`skill-creator`** — to iterate/benchmark `analyze-google-trends` (or a `trend-pulse` rename).
- **`dragan:analyze-google-trends`** — run it to observe current behavior before changing it.
- **`dragan:demo`** — only if you want to see/replay the factory flow that produced it (it rebuilds).
- **`publish-to-scratchpad`** — surface any investigation writeup as a rendered link (terminal-only operator).
- For the X-lane thread: **`security-and-hardening`**, **`read-the-damn-docs`**, **`deepwiki`**.

## 7. Sensitive data
None embedded here. The X lane (open thread) will involve `auth_token`/`ct0` cookies and possibly
`GROK_API_KEY`/`XAI_API_KEY` — **names only**; values live in the browser / Infisical / a gitignored
local file and must never be committed, logged, or pasted into a transcript.
