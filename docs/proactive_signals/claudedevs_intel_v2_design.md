# ClaudeDevs X Intel v2 — Design Doc

**Status:** Proposed (v1 design, awaiting implementation)
**Owner:** Kevin (product), Simone + Cody (operational)
**Branch:** `claude/monitor-twitter-feeds-WoLqF`
**Supersedes:** the existing `claude_code_intel` lane (services, rollup, replay) and its `agent_capability_library/claude_code_intel/current/` artifact

---

## 1. Why this exists

The current `@ClaudeDevs` X intelligence lane works mechanically — cron polls, packets get written, an email goes out, a 14‑day brief gets synthesized — but it does not actually produce the durable knowledge artifact it was built for. Three concrete failures in the current pipeline:

1. **The wiki is shallow.** `csi_url_judge.py:649` truncates each linked source to 3,000 characters before handing it to the classifier. Anthropic announcement docs routinely run 10–30K. The "knowledge" the system captures is a summary of an excerpt.
2. **The rolling brief is narrow and stale.** `claude_code_intel_rollup.py` hard‑codes a 14‑day window, caps to 18 contexts, filters out tier 1, and only rebuilds when fresh posts arrive (`run_report.py:321`). Backfill of historical packets does not retrigger synthesis.
3. **There is no demo path.** `demo_task` actions queue Task Hub items that no worker picks up. The reference implementations the system was meant to produce do not exist.

These are not the framing problems Kevin signed up for. The product is supposed to be a **durable, growing knowledge base of Claude Code / Claude Agent SDK capabilities**, with **executable reference implementations** that double as templates for client engagements. v2 redesigns the lane to deliver that product.

---

## 2. Product framing

The v2 system serves **three audiences** with **three views over one source of truth**:

| Audience | View | Optimization target |
|---|---|---|
| Kevin (operator, learner) | 28‑day rolling builder brief | Timely "what's new and what should I do about it" |
| UA agents (Simone, Cody, future agents) | Capability library — durable across full corpus | Executable knowledge: prompt patterns, code snippets, skill skeletons, ready‑to‑run demos |
| Both | The vault (LLM wiki) | Completeness, durability, queryability |

**The vault is the canonical product.** The brief and the library are derivative views generated from the vault, not parallel artifacts. Packets become disposable ingestion intermediates.

**Demos are reference implementations.** Kevin's stated business goal is to build agent systems for clients who want Claude Code / Claude Agent SDK functionality. Each demo carries a `business_relevance.md` that frames it as a candidate pattern for client work. Demos are not pedagogical toys — they are the prototype layer for paid engagements.

---

## 3. Architecture overview

Six phases, three of them autonomous on cron, two driven by Simone, one rare:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 0 — Dependency currency                                  (continuous) │
│   Anthropic package upgrades, smoke tests, version registry                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 1 — Discovery & research                                  (cron 2x/d) │
│   Poll → fetch full linked docs → research grounding → vault Memex update   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 2 — Briefing                          (Simone, heartbeat poll + ovr)  │
│   Read vault → author BRIEF/SOURCES/ACCEPTANCE/business_relevance → enqueue │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 3 — Implementation                            (Cody, persistent queue)│
│   Read brief → build → run in vanilla Claude Code env → capture output      │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 4 — Review & iterate                   (Simone, multi-loop director)  │
│   Run artifact → score → pass/iterate/defer → write FEEDBACK if iterating   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 5 — Memorialize                                       (occasional)    │
│   Promote proven patterns into reusable UA skills                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

The vault is the **contract surface** between Phase 1 and Phase 2. Simone never reads packets or raw tweets — she reads vault entity pages. That keeps discovery decoupled from briefing and makes generalization to additional lanes (Codex, Gemini, etc.) a matter of configuration.

---

## 4. The vault

### 4.1 Layout

```
artifacts/knowledge-vaults/claude-code-intelligence/
├── AGENTS.md                          # schema and operating contract
├── index.md                           # content catalog (LLM updates on every ingest)
├── log.md                             # chronological audit trail
├── overview.md                        # high-level synthesis
├── vault_manifest.json                # machine-readable manifest
│
├── raw/                               # IMMUTABLE — full-text fetched docs
│   ├── docs.anthropic.com/...
│   ├── github.com/anthropics/...
│   └── anthropic.com-news/...
│
├── sources/                           # IMMUTABLE — per-source pages
│   └── <slug>.md
│
├── entities/                          # MUTABLE — per-feature pages (LLM-maintained)
│   ├── <feature>.md
│   └── <feature>/
│       ├── demo_brief/                # workspace for Cody (BRIEF, SOURCES, ACCEPTANCE)
│       └── demos/                     # symlinks to /opt/ua_demos/<demo-id>/
│
├── concepts/                          # MUTABLE — per-concept pages (LLM-maintained)
│   └── <concept>.md
│
├── analyses/                          # MUTABLE — synthesis artifacts
│   ├── 28_day_brief/
│   │   ├── current.md                 # latest rolling brief
│   │   └── history/                   # snapshots
│   └── capability_library/
│       └── current/                   # bundles
│
├── infrastructure/                    # Phase 0 ops state
│   ├── installed_versions.md
│   ├── release_timeline.md
│   ├── version_drift.md
│   └── upgrade_failures/
│
├── lint/                              # health-check output
│   └── contradictions-YYYY-MM-DD.md
│
└── _history/                          # versioned snapshots of mutable pages
    └── <slug>/
        └── YYYY-MM-DD-HHMMSS.md
```

### 4.2 Page lifecycle (the append‑dominant invariant)

Every Phase 1 ingest takes one of three actions on each candidate page. The expected distribution under normal operation:

| Action | Frequency | Behavior |
|---|---|---|
| **CREATE** | ~80% | New `entities/<feature>.md` or `concepts/<concept>.md`. Linked from related existing pages. No rewrites. |
| **EXTEND** | ~15% | Append to existing page under a new dated section. Old content untouched. |
| **REVISE** | ~5% | Targeted rewrite of an existing page. Snapshot to `_history/`. Structured change-log entry in `log.md`. |

**Monitoring invariant:** if `_history/` fills faster than ~1 entry per ingest tick on average, something is wrong — either we're rewriting too aggressively or the LLM is misclassifying CREATE/EXTEND as REVISE. This is a first‑class health check.

### 4.3 Mitigations against page corruption

Four guardrails, layered. All four ship in v1:

1. **`raw/` and `sources/` are immutable.** Write‑once, never modified. Entity/concept pages are derivative; if they get corrupted, regenerate from sources.
2. **`_history/` snapshots on every REVISE.** Rollback is `cp` from history. The vault is also a git repo, so this is partly redundant — but explicit history files let the LLM read prior versions during the update pass and reason about the change.
3. **Structured change log per page touched.** Format:
   ```
   ## [2026-05-12T08:14:03Z] entities/Memory_Tool.md REVISE
   reason: source S introduces beta feature Z; previous claim about <quote> revised to <quote>.
   confidence: high
   sources: [sources/anthropic-memory-beta-launch.md, sources/github-anthropic-memory-readme.md]
   ```
4. **Lint sweep** runs monthly (low frequency, since append‑dominant means few contradictions expected). Flags issues to `lint/contradictions-YYYY-MM-DD.md`. Does not auto‑fix.

**Auto‑rewrite is the default** — ask‑permission is impractical at the cron cadence. Audit trail makes this safe.

### 4.4 Frontmatter contract for entity pages

Every `entities/<feature>.md` carries:

```yaml
---
title: "Skills (Claude Code)"
kind: entity
updated_at: "2026-05-12T08:14:03Z"
tags: [claude-code, skills, plugins, registration]
source_ids: [ext_2026050300_anthropic-skills, ext_2026050314_github-claude-skills]
provenance_kind: external_ingest
provenance_refs: ["packets/2026-05-03/081400__ClaudeDevs", ...]
confidence: high
status: active
briefing_status: pending          # pending | demo_worthy | informational | deferred | demo_built | demo_failed
endpoint_required: anthropic_native   # anthropic_native | any
min_versions:
  claude_code: ">=2.1.116"
  claude_agent_sdk_python: ">=0.5.0"
business_relevance: high           # high | medium | low | unknown
---
```

Simone's `cody-scaffold-builder` skill reads `briefing_status`, `endpoint_required`, `min_versions`, and `business_relevance` to decide what to do.

---

## 5. Phase 0 — Dependency currency

Pre‑condition for everything else. If the VPS isn't running the version a feature requires, the demo will fail in confusing ways. Phase 0 keeps the environment ahead of the feature pipeline.

### 5.1 Components

- **Daily sweep.** `scripts/dependency_currency_sweep.py` runs `uv pip list --outdated`, `npm outdated` against `web-ui/`, and probes installed `claude` CLI version. Writes `vault/infrastructure/version_drift.md`.
- **Release detection (in Phase 1).** Ingest classifier learns `action_type: release_announcement` and extracts a `(package, version, notable_features[])` tuple. Vault writes/updates `infrastructure/release_timeline.md`.
- **Upgrade queue.** Detected releases or drift produce `dependency_upgrade` Task Hub items. Higher priority than `demo_task` by construction.
- **Upgrade worker.** Cody (or a small dedicated system worker) takes the bump:
  1. Edit `pyproject.toml` / `package.json` / appropriate manifest on the working branch.
  2. `uv sync` and run smoke tests against **both** environments:
     - **ZAI smoke test** — verifies normal UA work still functions (`~/.claude/settings.json` mapping intact).
     - **Anthropic native smoke test** — verifies Max plan path still works (`/opt/ua_demos/_smoke/` minimal demo).
  3. On smoke pass: deploy via the existing `develop → main` GitHub Actions pipeline. Email Kevin with diff + smoke output.
  4. On smoke fail: roll back the manifest edit, write `infrastructure/upgrade_failures/<date>.md`, email Kevin.
- **Installed versions registry.** `vault/infrastructure/installed_versions.md` is the live record of what's actually on the VPS. Updated by the upgrade worker post‑deploy and verified by a periodic check.
- **Vault gate on demos.** Phase 2's `cody-scaffold-builder` reads each entity page's `min_versions` and checks `installed_versions.md`. If the VPS is behind, the demo task is held with `briefing_status: blocked_pending_upgrade` and the corresponding upgrade task is bumped to top of queue.

### 5.2 Scope

**Auto‑upgrade with smoke‑test gating + email** for all Anthropic‑adjacent packages. Specifically:

- `claude-code` (CLI, `npm i -g`)
- `claude-agent-sdk-python`
- `claude-agent-sdk-typescript`
- `anthropic` (Python SDK)
- `@anthropic-ai/sdk` (TypeScript SDK)

These are tightly coupled; bump them together. Kevin confirmed: "just make sure they're all up to date."

For non‑Anthropic deps (FastAPI, Next.js, etc.), keep current update cadence — they're not on the demo critical path.

---

## 6. Phase 1 — Discovery & research

### 6.1 Polling (largely unchanged from existing implementation)

- Cron `0 8,16 * * *` America/Chicago. Twice daily.
- Per‑handle checkpoint at `artifacts/proactive/claude_code_intel/state-<handle>.json`.
- Default handles: `[ClaudeDevs, bcherny]`. Configurable via `UA_CLAUDE_CODE_INTEL_X_HANDLES`.
- Per‑post X API rate limit handling stays as today.

### 6.2 URL enrichment — material rewrites from current behavior

Current `csi_url_judge.py` has two limits that need to lift:

| Limit | Current | v2 | Reason |
|---|---|---|---|
| `max_fetch` per post | 3 | 10 | Anthropic announcements often link 4–7 docs (changelog, docs page, example repo, blog). |
| `build_linked_context` chars per source | 3,000 | **None for analysis path** | Modern long‑context models can read full docs. The 3K cap is the single biggest signal‑destroying limit. |
| Per‑doc storage cap | 20,000 | 200,000 | Some Anthropic engineering posts approach 50K. Cap exists only to prevent runaway. |

The **classifier** (Phase 1's "what is this tweet about" pass) still gets a context budget, but that budget is filled by passing the full doc (or chunked over multiple LLM calls if needed), not by truncating to 3K up front.

### 6.3 Research grounding subagent

Triggered when:

- The tweet has no fetchable links, OR
- The classifier returns "low‑confidence / thin source" verdict, OR
- The tweet mentions a named feature/SDK term that doesn't yet have an entity page in the vault.

Allowlist (priority order):

1. `docs.anthropic.com` (especially `/en/release-notes/claude-code`)
2. `github.com/anthropics/claude-code` (releases, CHANGELOG, README)
3. `github.com/anthropics/claude-agent-sdk-python` and `claude-agent-sdk-typescript`
4. `anthropic.com/news`
5. `anthropic.com/engineering`
6. `support.anthropic.com`
7. General web (last resort, marked as such in provenance)

**Research only fires for tier ≥ 2 posts.** Noise tweets don't trigger research spending.

Every grounding source it finds gets ingested as a `sources/<slug>.md` page with `provenance_kind: research_grounded` and `provenance_refs` linking back to the originating tweet's source page. The wiki knows: "this concept page about Memory Tool was extended because @ClaudeDevs mentioned it on date X, and the analysis read these official docs Y, Z, W."

### 6.4 Memex update pass (per‑ingest)

After research grounding, the ingest LLM runs an entity/concept maintenance pass:

1. List all entity/concept pages potentially affected by this ingest (LLM call: "given this new source about <feature>, which existing pages might need updates? Which new pages are needed?").
2. For each candidate page, classify CREATE / EXTEND / REVISE.
3. Execute:
   - CREATE: new page with full default frontmatter.
   - EXTEND: append dated section to existing page. No rewrites.
   - REVISE: snapshot existing page to `_history/<slug>/YYYY-MM-DD-HHMMSS.md`, write rewritten page, append structured change-log entry.
4. Update `index.md` (content catalog) and `overview.md` (high-level synthesis). Append to `log.md`.

The hard rule: **`raw/` and `sources/` are never touched.** Only `entities/`, `concepts/`, `analyses/` are mutable.

---

## 7. Phase 2 — Briefing (Simone)

### 7.1 Trigger

Heartbeat poll. Simone, on her normal heartbeat tick, walks `vault/index.md` for entries with `briefing_status: pending`. She processes one at a time, so a flood of new entities after a Phase 1 burst doesn't blow up her tick budget.

**Operator override** available: a skill (or a structured email to Simone) can prioritize a specific entity, e.g., "build a demo for the new Skills feature this session."

### 7.2 Decision

For each pending entity, Simone decides:

- **`demo_worthy`** — feature is concrete enough to build a runnable demo. Author the workspace and enqueue Cody.
- **`informational`** — feature is real but not buildable as a demo (architectural change, deprecated path, organizational announcement). Mark and move on.
- **`deferred`** — official documentation insufficient at time of briefing. Re‑evaluated on a later ingest when more docs exist. The entity page records the reason.

Skip‑with‑deferral is the default when in doubt. Speculative demos are the failure mode we're avoiding — Cody's training cutoff would fill in gaps with stale or invented APIs.

### 7.3 Workspace artifacts

For each `demo_worthy` entity, Simone writes to `vault/entities/<feature>/demo_brief/`:

- **`BRIEF.md`** — feature briefing in plain language. Synthesized from the entity page + linked concept pages. This is what Cody reads first. Includes: what the feature is, why it matters, the canonical use case, named API surface (functions, classes, env vars).
- **`SOURCES/`** — the curated subset of `vault/raw/<doc>.md` files Cody actually needs. Full text, no summarization, no truncation. Cody can `grep` these.
- **`ACCEPTANCE.md`** — explicit success contract:
  ```
  - demo MUST import claude_agent_sdk and instantiate a SkillRegistry
  - demo MUST register at least one Skill subclass
  - demo MUST run end-to-end and print the skill's response
  - demo MUST NOT use any deprecated <feature_X> API per the doc
  - success criterion: stdout contains the string "skill response:"

  must_use_examples:
    - pattern: SkillRegistry initialization
      reference: SOURCES/docs.anthropic.com-skills.md#L84-L102
    - pattern: Skill subclass definition
      reference: SOURCES/github-claude-skills-readme.md#L23-L51
  ```
- **`business_relevance.md`** — Kevin‑facing rationale: "this pattern is useful for clients building X; the reference implementation should be structured so it can be lifted into a client engagement Y; if you need to choose between two valid implementations, prefer the one closer to typical client architecture Z."

### 7.4 Cody dispatch

Simone enqueues a Task Hub item:

```json
{
  "task_type": "cody_demo_task",
  "assigned_to": "cody",
  "queue_policy": "wait_indefinitely",
  "workspace_dir": "/opt/ua_demos/<demo-id>/",
  "brief_dir": "vault/entities/<feature>/demo_brief/",
  "endpoint_required": "anthropic_native",
  "wall_time_max_minutes": 30,
  "feature_entity_path": "vault/entities/<feature>.md",
  "iteration": 1
}
```

The persistent queue means: if Cody is busy, the task waits. No retries‑then‑give‑up. This is intentional.

---

## 8. Phase 3 — Implementation (Cody)

### 8.1 Execution environment — the dual‑environment requirement

This is the single most important environmental constraint in the system. Two execution profiles exist on the VPS, and Cody must run in the right one:

| Profile | Used for | Config home | Auth | Models |
|---|---|---|---|---|
| **ZAI‑mapped (default UA)** | All normal UA work, including Cody's regular coding tasks | `~/.claude/settings.json` (with full env block, hooks, plugins) | `ANTHROPIC_AUTH_TOKEN` env (ZAI key) | GLM via ZAI proxy |
| **Anthropic native (demos only)** | Demo execution under Phase 3 | Project-local `.claude/settings.json` inside `/opt/ua_demos/<demo-id>/` (clean, no env, no hooks, no UA plugins) | Max plan OAuth session (`claude /login` with Kevin's Max account) | Real Anthropic Claude models, full feature surface |

**Why both must coexist:** the ZAI mapping gives Kevin cheap GLM coding for routine UA work — that's the operating model and we're not breaking it. Demos break out of the mapping because they exercise *brand‑new Anthropic features* that the ZAI proxy may not have implemented yet. Running a new‑feature demo against ZAI would be a false negative — we'd think the demo is broken when really the endpoint doesn't support the feature.

### 8.2 Vanilla Claude Code requirement

The polluted `~/.claude/settings.json` includes more than just the endpoint mapping. Inspection reveals:

- `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_DEFAULT_*_MODEL` (the ZAI redirect)
- A full hook chain pointing at `~/.claude/agent-flow/hook.js` (fires on every tool call)
- Many enabled plugins (some may inject context or change model behavior)
- `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS: 1` (changes runtime shape)
- `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: 1` (suppresses telemetry features may rely on)

Any of these can break a demo for reasons unrelated to the feature being demonstrated. The demo environment is therefore **vanilla Claude Code** — minimal project‑local `.claude/settings.json` with no env block, no hooks, no plugins, no experimental flags.

### 8.3 Demo directory structure

```
/opt/ua_demos/
├── _smoke/                            # Phase 0 smoke-test demos
├── _scaffold_template/                # template for new demos
│   └── .claude/
│       └── settings.json              # vanilla, minimal
└── <demo-id>/                         # one per demo
    ├── .claude/
    │   └── settings.json              # vanilla — no env, no hooks, no plugins
    ├── BRIEF.md                       # copied from vault by Simone
    ├── SOURCES/                       # copied from vault by Simone
    ├── ACCEPTANCE.md                  # copied from vault by Simone
    ├── business_relevance.md          # copied from vault by Simone
    ├── pyproject.toml                 # demo's own deps
    ├── src/                           # Cody's code lives here
    ├── BUILD_NOTES.md                 # Cody documents gaps/decisions
    ├── run_output.txt                 # captured stdout from successful run
    └── manifest.json                  # demo metadata (versions used, endpoint hit, success status)
```

After a successful demo, Simone symlinks `vault/entities/<feature>/demos/<demo-id>` → `/opt/ua_demos/<demo-id>/` so the wiki points at the live artifact.

### 8.4 Authentication

**One‑time VPS setup:** the VPS user that runs Cody's demo subprocesses must have done `claude /login` with Kevin's Max plan account at least once. The session token persists in the user's home directory (under whatever Claude Code's auth storage path is). Cody and Simone never authenticate — they inherit the existing logged‑in session.

**Critical:** no `ANTHROPIC_AUTH_TOKEN` env var should leak into the demo subprocess. If one is set in the parent environment, the demo wrapper must `unset` it before invoking Claude Code. The vanilla project‑local settings.json plus the unset is what guarantees the Max plan path is used.

### 8.5 The `cody-implements-from-brief` contract

Cody's contract when picking up a `cody_demo_task`:

1. `cd` into the workspace dir. Verify project‑local `.claude/settings.json` is vanilla (no env, no hooks, no plugins).
2. Read `BRIEF.md`, `ACCEPTANCE.md`, `business_relevance.md`.
3. Read at least the primary doc in `SOURCES/` before writing any code.
4. Build the demo. Invoke Claude Code from inside the workspace dir so project‑local settings take effect.
5. Run via `uv run` (or appropriate runner). Capture stdout to `run_output.txt`.
6. **Hard rule:** if the docs don't show how to do something, document the gap in `BUILD_NOTES.md`. Do not invent API surface. Cody's training cutoff predates the feature — invented code will look plausible and be wrong.
7. Write `manifest.json`:
   ```json
   {
     "demo_id": "...",
     "feature": "...",
     "endpoint_hit": "https://api.anthropic.com",
     "model_used": "claude-...-...",
     "claude_code_version": "...",
     "claude_agent_sdk_version": "...",
     "wall_time_seconds": 423,
     "acceptance_passed": true,
     "iteration": 1
   }
   ```
8. If acceptance criteria pass on Cody's local run, mark task complete and notify Simone. If not, notify Simone with `BUILD_NOTES.md` populated.

Wall time: 30 minutes per attempt. No budget cap. Persistent queue means Cody can be re‑dispatched as many times as Simone decides.

---

## 9. Phase 4 — Review & iterate (Simone)

### 9.1 Review

When Cody returns a completed demo:

1. Simone runs the artifact herself, in the same vanilla environment, to verify reproducibility.
2. Simone checks `manifest.json.endpoint_hit` — must be Anthropic native for `endpoint_required: anthropic_native` demos. If it accidentally hit ZAI (some env leak), demo is rejected with `endpoint_mismatch` and re‑queued with corrective directive.
3. Simone scores against `ACCEPTANCE.md` requirements one by one.
4. Simone reads `BUILD_NOTES.md` to surface any gaps Cody documented.
5. Simone scores against `business_relevance.md` — does the implementation match the client‑relevance shape we wanted?

### 9.2 Decision

- **Pass:** commit the demo, append `## Demos` section to `vault/entities/<feature>.md`, update capability library, mark `briefing_status: demo_built`.
- **Iterate:** write `FEEDBACK.md` (prioritized list of changes anchored to acceptance criteria), increment `iteration`, re‑queue Cody.
- **Defer:** if iteration count exceeds reasonable bound or feature genuinely can't be tested (waiting on Anthropic rollout, env access issue), mark `briefing_status: deferred` with reason. Move on. Re‑visited in future heartbeat ticks.

### 9.3 Failure handling

Failed demos go to `vault/entities/<feature>/failed_demos/<demo-id>/` with their full state intact. Failure is an expected outcome — frontier features are flaky and the system is designed to learn from breakage. Simone summarizes recurring failure patterns in monthly `analyses/failure_modes.md` so Kevin sees what's consistently breaking.

---

## 10. The 28‑day rolling brief

Derivative of the vault, regenerated on a separate cadence from Phase 1.

### 10.1 Window and content

- **28 days** (configurable via `UA_CLAUDE_CODE_INTEL_BRIEF_WINDOW_DAYS`). Up from the existing 14.
- **No 18‑item cap.** Brief synthesis reads everything CREATE'd or EXTEND'd in the window.
- **Content:**
  - `## What's new` — entity pages CREATE'd in window, grouped by category.
  - `## What changed` — entity pages REVISE'd in window (rare by design — append‑dominant).
  - `## What we built` — demos that landed in window, with links.
  - `## What we tried that failed` — failed demo summaries with recurring failure modes.
  - `## What's next` — pending briefings + deferred demos, prioritized.

### 10.2 Trigger

**Decoupled from new‑posts arrival.** The current system only rebuilds the brief when fresh posts arrive (`run_report.py:321`). v2 rebuilds on:

- Every Phase 1 cron tick that produces *any* vault mutation (CREATE/EXTEND/REVISE).
- Every Phase 4 demo completion.
- On explicit operator request (skill or email).

Backfills now trigger brief rebuilds correctly because the trigger is "vault mutated," not "new post arrived."

### 10.3 Storage

`vault/analyses/28_day_brief/current.md` and `current.json`. Snapshot to `history/<timestamp>.md` on every regeneration. Repo mirror at `agent_capability_library/claude_code_intel/current/rolling_28_day_report.md` for git‑tracked history.

---

## 11. The capability library

Durable across the **full corpus**, not bound to 28 days.

### 11.1 Structure

```
agent_capability_library/claude_code_intel/
├── current/
│   ├── index.json                     # bundle catalog
│   └── bundles/
│       └── <bundle-id>/
│           ├── bundle.json
│           ├── bundle.md
│           ├── prompt_patterns.md
│           ├── workflow_recipes.md
│           ├── code_snippets/
│           ├── skill_skeletons/
│           ├── ua_adaptation_notes.md
│           └── linked_demos/         # symlinks into vault/entities/<feature>/demos/
└── history/
    └── <date>/                       # full snapshots
```

### 11.2 Generation

Re‑synthesized on the same triggers as the brief, but reads the **whole vault**, not a windowed slice. Bundles are organized by capability cluster (e.g., "Skills + Plugins + Marketplace," "Memory Tool + Managed Agents," "Hooks + Subagents"), not by date. Old bundles persist in `history/` for git‑tracked reproducibility.

### 11.3 Consumer contract

Other UA agents pull from `agent_capability_library/claude_code_intel/current/index.json` to discover what Claude Code patterns exist. When an agent needs to "build a Claude Code skill," it can read the relevant bundle's `skill_skeletons/` for ready‑to‑adapt scaffolding. When an agent needs to "register a hook," `prompt_patterns.md` and `code_snippets/` show idiomatic usage.

---

## 12. Backfill plan (one‑time, ships with v1)

The existing vault and capability library were built by the old pipeline (3K context cap, 14‑day window, no research grounding, no Memex updates). They cannot be repaired in place. Plan: **rebuild via parallel‑vault staging.**

### 12.1 Procedure

1. **Build v2 pipeline.** Phase 0 + Phase 1 (with research grounding and Memex updates).
2. **Identify backfill input.** All packets under `artifacts/proactive/claude_code_intel/packets/<date>/<stamp>__<handle>/` that we want to re‑process. Probably "all of them" — packets are cheap to keep and the seen‑state machinery prevents re‑queueing Task Hub items.
3. **Provision parallel vault.** Empty directory at `artifacts/knowledge-vaults/claude-code-intelligence-v2/`.
4. **Replay script.** `scripts/claude_code_intel_backfill_v2.py` walks every packet through the new Phase 1 ingest, writing into the parallel vault. Reuses existing fetched URL content under `packets/.../url_enrichment/` — no need to re‑hit the web for fetches we already have. Falls back to fresh fetches when the cache is missing.
5. **Inspect.** Manual review of the parallel vault. Spot‑check entity pages, source coverage, change log integrity.
6. **Swap.** Atomic rename:
   - `claude-code-intelligence/` → `claude-code-intelligence-v1-archive/`
   - `claude-code-intelligence-v2/` → `claude-code-intelligence/`
7. **Regenerate brief and capability library** from the new vault.
8. **Notify Kevin** with a diff summary (entity count delta, source count delta, surfaced contradictions).

### 12.2 Cost

Backfill is bounded — once. Per packet: maybe 10–20 LLM calls (research grounding, classifier, Memex update). At ~100 packets, that's a few thousand calls. Fine. Phase 0 and Phase 1 both use ZAI mapping by default (cheap); only Phase 3 demo execution uses the Anthropic Max plan.

### 12.3 Rollback

Old vault is preserved as `claude-code-intelligence-v1-archive/` in case the new vault has unexpected gaps. Reverse the swap if needed.

---

## 13. Generalization to additional lanes

The pipeline is parameterized by:

- **Handle list** (`UA_CLAUDE_CODE_INTEL_X_HANDLES`)
- **Allowlist for research grounding**
- **Vault slug** (output directory)
- **Repo capability library slug**

Adding "OpenAI Codex intel," "Gemini intel," etc. is configuration:

```
artifacts/knowledge-vaults/openai-codex-intelligence/
artifacts/knowledge-vaults/gemini-intelligence/
agent_capability_library/openai_codex_intel/
agent_capability_library/gemini_intel/
```

Phase 0, Phase 1, Phase 2, Phase 3, Phase 4 logic is shared. Per‑lane config lives in a single `lanes.yaml`:

```yaml
lanes:
  claude-code-intelligence:
    handles: [ClaudeDevs, bcherny]
    allowlist:
      - docs.anthropic.com
      - github.com/anthropics/*
      - anthropic.com/news
    cron: "0 8,16 * * *"
    timezone: America/Chicago
  openai-codex-intelligence:
    handles: [OpenAIDevs, OpenAI, sama]
    allowlist:
      - platform.openai.com/docs
      - github.com/openai/*
      - openai.com/blog
    cron: "0 9,17 * * *"
    timezone: America/Chicago
```

Demo environment for Codex demos would use a separate Anthropic‑equivalent setup (likely an OpenAI API key‑authenticated environment). Out of scope for v1; flagged for v2 of v2.

---

## 14. Skills extracted from this pipeline

Listed in dependency order. Each is a candidate for memorialization once the pattern proves itself.

| Skill | Owner | Phase | Purpose |
|---|---|---|---|
| `cody-scaffold-builder` | Simone | 2 | Read vault entity → write BRIEF/SOURCES/ACCEPTANCE/business_relevance. Highest leverage. |
| `cody-task-dispatcher` | Simone | 2 | Enqueue Cody task with persistent queue policy. |
| `cody-progress-monitor` | Simone | 3 | Pull Cody's run state, surface blockers. |
| `cody-work-evaluator` | Simone | 4 | Run artifact, score against acceptance, decide pass/iterate/defer, write FEEDBACK. |
| `vault-demo-attach` | Simone | 4 | Symlink demo into entity page, update capability library. |
| `cody-implements-from-brief` | Cody | 3 | Cody's contract for reading the workspace, building, capturing output, writing BUILD_NOTES. |
| `dependency-currency-sweep` | System | 0 | Daily outdated-package sweep + drift report. |
| `anthropic-package-upgrade` | Cody or System | 0 | Bump version, smoke-test, deploy, email Kevin. |
| `vault-lint-sweep` | System | (monthly) | Contradiction detection across vault. Reports only, no auto-fix. |
| `vault-backfill-replay` | System | (one-time) | Replay packets into a parallel vault for v1 → v2 migration. |

---

## 15. Risk matrix

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Memex update corrupts a good entity page | Medium | High | `_history/` snapshots; sources/raw immutable; structured change log; lint sweep. |
| Demo fails because ZAI mapping leaked into Cody's environment | High initially | High | Vanilla project-local `.claude/settings.json`; unset `ANTHROPIC_AUTH_TOKEN` in wrapper; `manifest.json.endpoint_hit` verification by Simone. |
| Demo fails because feature requires beta env var Cody doesn't know about | Medium | Low | Multi-loop iteration: Cody documents gap → Simone fixes → re-queue. Expected and accepted. |
| Anthropic upgrade breaks UA's normal ZAI-mapped operation | Low | High | Phase 0 smoke tests both environments before committing the bump. Rollback on either smoke fail. |
| Research grounding fetches inappropriate content (rabbit hole) | Low | Low | Allowlist enforces source priority; tier ≥ 2 filter prevents noise tweets from triggering research. |
| Backfill produces a worse vault than the one we have | Low | Medium | Parallel-vault staging. Inspect before swap. Old vault archived, not deleted. |
| Cody's training cutoff causes invented API in demos | Medium | High | Hard rule: no invention. Cody documents gaps in BUILD_NOTES.md instead. Simone verifies. |
| Max plan usage cap hit by demo iteration | Low | Medium | Defer after enough iteration loops. Failed demos cost less than passing demos. |
| Polluted `~/.claude/settings.json` credentials get committed | Medium | High | Demo workspaces never write to user-global config. Project-local settings only. CI grep for known-bad token patterns. |
| Generalization to Codex/Gemini surfaces design assumptions baked into Anthropic-only flow | Medium | Low | Generalization is explicitly v2-of-v2. Refactor when we get there. |

---

## 16. Implementation sequence

PR breakdown, in dependency order:

1. **PR 1 — `csi_url_judge` rewrite.** Lift 3K context cap, 20K storage cap, 3-fetch limit. Add full-doc passthrough to classifier. Verify existing pipeline still works end-to-end.
2. **PR 2 — Vault Memex update pass.** Implement CREATE/EXTEND/REVISE decision in `wiki/core.py`. Add `_history/` snapshots, structured change-log entries. Update `wiki_ingest_external_source` to call the maintenance pass.
3. **PR 3 — Research grounding subagent.** New module `services/research_grounding.py`. Allowlist enforcement. Tier ≥ 2 gate. Integrate into Phase 1 ingest.
4. **PR 4 — 28-day brief decoupling.** Lift hard-coded window to env var. Remove 18-item cap. Decouple regeneration trigger from "new posts arrived." Read from vault, not from packets.
5. **PR 5 — Capability library full-corpus mode.** Drop window filter. Bundle by capability cluster, not by date. Generate from vault.
6. **PR 6 — Phase 0 dependency currency.** `dependency_currency_sweep.py`. Release-detection classifier extension. Upgrade worker (Cody contract). Smoke tests for both environments. Email integration.
7. **PR 7 — Demo execution environment.** Provision `/opt/ua_demos/` on VPS. `_scaffold_template/` with vanilla settings.json. VPS setup runbook (Max plan login). Smoke demo.
8. **PR 8 — Simone Phase 2 skills.** `cody-scaffold-builder`, `cody-task-dispatcher`. Heartbeat poll integration. Operator override skill.
9. **PR 9 — Cody Phase 3 skill.** `cody-implements-from-brief`. Workspace contract. Manifest writing. Endpoint verification.
10. **PR 10 — Simone Phase 4 skills.** `cody-progress-monitor`, `cody-work-evaluator`, `vault-demo-attach`. Multi-loop iteration with FEEDBACK.md.
11. **PR 11 — Lanes config.** `lanes.yaml`. Refactor lane-specific code paths to read from config. Verify Claude Code lane still works identically. (Generalization scaffolding only — additional lanes are separate PRs later.)
12. **PR 12 — Backfill replay script.** `scripts/claude_code_intel_backfill_v2.py`. Parallel-vault staging. Swap procedure documented in runbook.
13. **PR 13 — Vault lint sweep.** Monthly cron. Contradiction detection. Reports only.

PR 1, 2, 3, 4, 5 can ship in roughly that order without breaking the existing pipeline. PR 6 is independent and can ship in parallel with the wiki work. PR 7–10 form the demo critical path and depend on PR 1–5 being live. PR 11 is mechanical refactor. PR 12 runs once after all of the above land. PR 13 is hygiene.

---

## 17. Open questions deferred to implementation

These were discussed in the design conversation and intentionally not pre‑decided. They'll be resolved during build:

- **Exact LLM model choice for Memex update pass.** Probably Claude Sonnet via the ZAI mapping for cost — but the pass needs long context, so Claude with 1M context window is a candidate. Decide during PR 2.
- **Whether the upgrade worker should be Cody or a dedicated system worker.** Cody is fine for v1 if she's reliably available. If Phase 0 cadence overwhelms her capacity, split it out. Decide during PR 6.
- **Exact `feature_flags_required` schema for entity frontmatter.** Premature today. Add after 3–4 demos teach us which kinds of flags actually matter. Until then, multi-loop iteration handles config gaps.
- **Whether Phase 2 should batch (process N pending entities per heartbeat) or one-at-a-time.** Start one-at-a-time. Batch if Simone's queue grows faster than her tick rate.
- **Demo lifecycle — when does an old demo get retired?** A demo built against `claude-code@2.1` may be obsolete when `claude-code@3.0` lands. Need a re-validation policy. Defer until we have demos to age.

---

## 18. What stays the same

For clarity on what we're not changing:

- **Cron schedule** (`0 8,16 * * *` America/Chicago). Twice a day stays correct.
- **Per-handle checkpoint** state file format and location.
- **X API auth** (bearer token + OAuth fallback).
- **AgentMail-based operator email** path. Just the *content* and *trigger* of the email change.
- **The polluted `~/.claude/settings.json`.** UA's normal ZAI-mapped operation continues unchanged. v2 adds a parallel demo path, doesn't disrupt the existing one.
- **GitHub Actions deploy pipeline** (`develop → main`). Phase 0's upgrade worker uses this, doesn't replace it.
- **Task Hub** as the queueing substrate. Cody pickup happens through normal Task Hub semantics; the only new thing is the persistent-queue policy.

---

## 19. Success criteria for v1

The system is working when:

1. A new ClaudeDevs tweet about a real Anthropic feature flows through Phase 1 within one cron tick, producing a vault entity page that cites the official docs in full.
2. Simone picks up that entity page on her next heartbeat tick and produces a demo workspace with a real ACCEPTANCE contract.
3. Cody builds the demo in `/opt/ua_demos/<demo-id>/`, runs it against real Anthropic endpoints (verified via `manifest.json.endpoint_hit`), and produces working output.
4. Simone judges the demo, links it from the vault entity page, and marks it `demo_built`.
5. The next 28-day brief surfaces this as a new capability in the "What we built" section.
6. The capability library's relevant bundle includes runnable code derived from the demo.
7. A Phase 0 release announcement triggers an SDK upgrade with smoke tests passing against both environments and an email to Kevin documenting the bump.
8. Backfill of historical packets (one-time) produces a coherent vault that's materially richer than the v1-archive.

When all eight happen end-to-end, ship.
