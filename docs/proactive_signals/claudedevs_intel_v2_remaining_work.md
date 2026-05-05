# ClaudeDevs Intel v2 — Remaining Work Plan

> **Status:** Living document. Updated after each PR ships.
> **Last updated:** 2026-05-05 (after PR 6b ship at SHA `564189c6`)
> **Owner:** AI Coder on `feature/latest2`, with operator gates for `/ship` cycles
> **Companion:** [`claudedevs_intel_v2_design.md`](claudedevs_intel_v2_design.md) (the original 13-PR design doc)

---

## Why this doc exists

The original v2 design doc laid out 13 PRs in a clean sequence. As we executed
the first 8, three things happened:

1. Some PRs shipped scaffolding only and explicitly deferred the "wire it in"
   work to follow-up PRs. Those follow-ups need to be tracked.
2. The shipping process surfaced new PRs that weren't in the original plan
   (e.g., PR 6b's auto-trigger from release-announcement detection).
3. The CLI-vs-SDK auth wrinkle turned PR 7 into PR 7 + PR 7b.

This doc is the durable, single-source-of-truth catalog of what's left so
nothing falls through the cracks. The original design doc is unchanged; this
is the reconciled execution view.

---

## What's already shipped (12 PRs, all in production)

| PR | Commit | Subject |
|---|---|---|
| PR 1 | `ff1867e` | csi_url_judge: lifted v1 truncation caps |
| PR 2 | `200d4d9` | Wiki Memex update primitives (CREATE/EXTEND/REVISE + `_history/` snapshots + change log) |
| PR 3 | `b01783f` | Research grounding subagent (tier ≥ 2 gate, four trigger reasons, allowlist-enforced fetch) |
| PR 4 | `ed7a52a` | Rolling brief: 14→28 day window, removed 18-cap, decoupled rebuild trigger, `--rebuild-brief` CLI flag |
| PR 5 | `86b4fdb` | Capability library full-corpus mode (env-switchable, `source_mode` in index.json) |
| PR 11 | `c96c8a5` | `intel_lanes.yaml` + Pydantic-validated loader (Claude Code enabled; Codex/Gemini disabled templates) |
| PR 6a | `326da0d` | Phase 0 dependency-currency observation (sweep CLI, vault infrastructure pages, release-announcement detection) |
| PR 7 | `831db36` | Demo workspace scaffolding (`/opt/ua_demos/` provisioner + vanilla settings + smoke template) |
| PR 7b | `31f5253` | Smoke demo CLI rewrite + `pyproject.toml` (CLI-vs-SDK auth wrinkle) |
| PR 6b | `c31e1b5` | Phase 0 upgrade actuator (pyproject surgery + dual-environment smoke gating + email + rollback) |
| docs (1st) | `54e4226` | CSI canonical doc v2 update + new dual-environment reference + index registration |
| docs (2nd) | `b1cf9f7` | "READ FIRST" callouts in `CLAUDE.md`, `docs/README.md`, runbook |

**Production SHA at time of writing:** `564189c6` on `main`.

---

## Cross-reference: original design doc § 16 → reconciled execution

The original design doc listed 13 PRs in `claudedevs_intel_v2_design.md` § 16.
Several shipped as scaffolding only with the wiring deferred. Here's the full
mapping so nothing is lost:

| Original (design doc § 16) | Reconciled in execution | Status |
|---|---|---|
| PR 1 — csi_url_judge rewrite | PR 1 | ✅ shipped (`ff1867e`) |
| PR 2 — Vault Memex update pass (incl. wire `wiki_ingest_external_source`) | PR 2 (primitives) + **PR 15** (wiring) | ⚠️ scaffolding shipped (`200d4d9`); **wiring pending as PR 15** |
| PR 3 — Research grounding subagent (incl. integrate into Phase 1 ingest) | PR 3 (subagent) + **PR 16** (wiring) | ⚠️ scaffolding shipped (`b01783f`); **wiring pending as PR 16** |
| PR 4 — 28-day brief decoupling | PR 4 | ✅ shipped (`ed7a52a`) |
| PR 5 — Capability library full-corpus mode | PR 5 | ✅ shipped (`86b4fdb`) |
| PR 6 — Phase 0 dependency currency (sweep + actuator + email) | PR 6a (sweep) + PR 6b (actuator) + **PR 6c** (auto-trigger) | ⚠️ 6a/6b shipped (`326da0d` / `c31e1b5`); **auto-trigger pending as PR 6c** |
| PR 7 — Demo execution environment (provision + smoke) | PR 7 + PR 7b (CLI fix) | ✅ shipped (`831db36` / `31f5253`) |
| PR 8 — Simone Phase 2 skills | PR 8 | ⬜ pending |
| PR 9 — Cody Phase 3 skill | PR 9 | ⬜ pending |
| PR 10 — Simone Phase 4 skills | PR 10 | ⬜ pending |
| PR 11 — Lanes config (scaffolding + refactor existing paths to read it) | PR 11 (scaffolding) + **PR 17** (wiring) | ⚠️ scaffolding shipped (`c96c8a5`); **wiring pending as PR 17** |
| PR 12 — Backfill replay script | PR 12 | ⬜ pending |
| PR 13 — Vault lint sweep | PR 13 | ⬜ pending |

### Deferred decisions from § 17 — where each resolves

The original doc explicitly deferred five decisions to implementation. Each is mapped to the PR where it gets resolved:

| Open question (§ 17) | Resolution |
|---|---|
| Exact LLM model choice for Memex update pass | **Resolves in PR 15.** Probably ZAI-mapped Claude Sonnet for cost; switch to direct Claude with 1M context if the pass needs more. |
| Whether the upgrade worker should be Cody or a dedicated system worker | **Resolved in PR 6b** as a callable CLI (neither Cody nor cron yet). PR 6c upgrades to auto-trigger via the existing CSI cron, no Cody dependency. |
| Exact `feature_flags_required` schema for entity frontmatter | **Deferred to post-PR 9.** Add after 3–4 real demos teach us which flag types actually matter. Multi-loop iteration handles config gaps in the meantime. |
| Whether Phase 2 should batch or process one-at-a-time | **Resolves in PR 8.** Start one-at-a-time per heartbeat; batch only if Simone's queue grows faster than her tick rate. |
| Demo lifecycle — when does an old demo get retired? | **Tracked as PR 18 below.** Defer until we have enough demos to age. |

### Cross-pipeline additions surfaced during execution

Two PRs were added beyond the original 13 because real-world execution surfaced needs the design doc didn't cover:

- **PR 14** — YouTube tutorial endpoint_profile generalization. Kevin asked on 2026-05-05 whether YouTube tutorial pipeline implementations that exercise Anthropic features should also use the dual-environment pattern. Yes, conditionally. Deferred to end of queue.
- **PR 18** — Demo lifecycle / retire policy (from § 17 deferred). Decide when an old demo built against `claude-code@2.1` should be re-validated against `@3.0`. Defer until we have demos to age.

---

## What's left — phased plan (11 remaining PRs)

The remaining work is organized into **four phases** that respect dependencies.
Within each phase, PRs are listed in execution order.

### Phase A — Wire the v2 ingest pipeline (so the vault actually gets v2-style content)

These are the latent integration gaps from PRs 2, 3, and 11. Each of those
shipped scaffolding only; the actual rewiring of existing code paths to call
the new primitives was explicitly deferred.

**Why this phase first:** without these, the vault keeps getting v1-style
content even though the v2 primitives exist. Cody and Simone (Phase B) read
from the vault — if the vault isn't v2-shaped, everything downstream is
less effective than it could be.

#### PR 15 — Memex wiring into ClaudeDevs replay path
- **Need:** `wiki_ingest_external_source` writes a per-source page only. PR 2's
  `memex_apply_action` (CREATE/EXTEND/REVISE on entity/concept pages with
  `_history/` snapshots) is never called.
- **Scope:** Modify `claude_code_intel_replay.ingest_packet_into_external_vault`
  to (1) extract candidate entity/concept names from the action's
  `release_info` + classifier reasoning + linked source titles, (2) call
  `memex_apply_action` for each candidate, (3) record the action results in
  the candidate ledger.
- **Risk:** Low — Memex primitives are pure additive; if extraction fails the
  source page still gets written by the existing path.
- **Tests:** Mocked replay against synthetic packet; assert entity pages get
  created, log.md gets structured entries, `_history/` populated on REVISE.
- **Estimate:** Medium (~400 lines + tests).

#### PR 16 — Research grounding wiring into Phase 1 ingest
- **Need:** `claude_code_intel.run_sync` does URL enrichment via
  `csi_url_judge`, but never calls `research_grounding.execute_research`.
  Tweets with no links / thin links don't trigger the official-docs-first
  fallback that PR 3 built.
- **Scope:** In `run_sync`, after the existing URL enrichment for tier ≥ 2
  posts, call `research_grounding.build_research_request` and (if triggered)
  `execute_research`. Merge the resulting `EnrichmentRecord`s into
  `linked_context` so the classifier and (post PR 15) the Memex pass see them.
- **Risk:** Low–medium. The research subagent uses the existing
  `fetch_url_content`, so the storage caps and timeout behavior are uniform.
  Risk is the extra LLM call cost when many tweets trigger research.
  Mitigation: keep tier ≥ 2 gate strict.
- **Tests:** Mock `execute_research`; assert it's called only for tier ≥ 2 +
  trigger conditions; assert returned records flow into `linked_context`.
- **Estimate:** Medium (~250 lines + tests).

### Phase B — Demo orchestration (the Simone↔Cody loop)

This is the headline value of v2 — the autonomous demo build pipeline. All
three PRs depend on Phase A landing first so the vault has the entity pages
and grounded sources Simone needs.

#### PR 8 — Simone Phase 2 skills (`cody-scaffold-builder`, `cody-task-dispatcher`)
- **Need:** Convert a vault entity page into a fully populated demo workspace
  + queue a Cody task.
- **Scope:** Two new skills under `.claude/skills/`:
  - `cody-scaffold-builder` reads `vault/entities/<feature>.md`, copies
    relevant `vault/raw/` docs into `/opt/ua_demos/<demo-id>/SOURCES/`,
    authors `BRIEF.md` / `ACCEPTANCE.md` / `business_relevance.md` from
    the entity page + linked sources, calls `provision_demo_workspace`
    (which already exists from PR 7).
  - `cody-task-dispatcher` enqueues a `cody_demo_task` Task Hub item
    with the workspace path, persistent-queue policy.
- **Risk:** Medium — depends on Task Hub conventions for queue policy and
  Cody task payloads.
- **Tests:** End-to-end with a fake entity page; assert workspace populated;
  assert task queued with right shape.
- **Estimate:** Large (~600 lines + tests + skill definitions).

#### PR 9 — Cody Phase 3 skill (`cody-implements-from-brief`)
- **Need:** Cody's contract for actually building the demo inside the workspace.
- **Scope:** New skill `.claude/skills/cody-implements-from-brief/`. Cody:
  1. `cd` into the workspace dir (verifies vanilla settings via
     `verify_vanilla_settings`).
  2. Reads `BRIEF.md` / `ACCEPTANCE.md` / `business_relevance.md`.
  3. Reads at least the primary doc in `SOURCES/`.
  4. Builds the demo. Invokes `claude` CLI from inside the workspace so
     project-local settings take effect.
  5. Captures stdout, writes `manifest.json` (endpoint hit, model, versions),
     `run_output.txt`, `BUILD_NOTES.md`.
  6. Marks task complete (or notifies Simone with `BUILD_NOTES.md` populated).
- **Risk:** High — end-to-end integration test requires `claude /login` (✅
  already done), but the actual demo execution path hasn't been exercised
  end-to-end yet. Expect surprises.
- **Tests:** Unit tests for the skill's contract checks. Real end-to-end
  test deferred to operational shake-down on the VPS.
- **Estimate:** Large (~500 lines + tests + extensive skill SKILL.md).

#### PR 10 — Simone Phase 4 skills (`cody-progress-monitor`, `cody-work-evaluator`, `vault-demo-attach`)
- **Need:** The multi-loop director. Simone reads Cody's output, scores
  against `ACCEPTANCE.md`, decides pass/iterate/defer, writes `FEEDBACK.md`
  on iterate, attaches the demo to the vault entity page on pass.
- **Scope:** Three new skills:
  - `cody-progress-monitor` — pulls Cody's task state, surfaces blockers.
  - `cody-work-evaluator` — runs the artifact, scores against ACCEPTANCE,
    decides pass/iterate/defer, writes `FEEDBACK.md`.
  - `vault-demo-attach` — once a demo passes, append `## Demos` section to
    `vault/entities/<feature>.md` and update capability library.
- **Risk:** Medium. The multi-loop iteration is conceptually clean but the
  scoring rubric (acceptance criteria → pass/fail) may need tuning.
- **Tests:** Mocked artifact running; pass/iterate/defer transitions.
- **Estimate:** Large (~600 lines + tests + skill SKILL.mds).

### Phase C — Operations & generalization

#### PR 6c — Auto-trigger upgrade actuator from release-announcement detection
- **Need:** PR 6a wired `release_announcement` action_type into the classifier;
  PR 6b built the actuator. Currently the human reads the email about a new
  release and runs the actuator manually. Should auto-fire.
- **Scope:** When `claude_code_intel_run_report` finishes a tick that contains
  any `action_type=release_announcement` actions for Anthropic-adjacent
  packages, automatically invoke `apply_upgrade(package, version)` for each.
  Email Kevin separately for the release detection AND the upgrade outcome.
- **Risk:** Low — both halves already exist; this is wiring.
- **Tests:** Mock both halves; assert the wiring fires under the right
  conditions and not otherwise.
- **Estimate:** Small (~150 lines + tests).

#### PR 12 — Backfill replay script with parallel-vault staging
- **Need:** Existing v1 packets need to be replayed through the v2 pipeline
  (with PRs 15, 16 wired) to populate the vault with v2-shape content. Per
  the design doc §12 backfill plan B: replay into a parallel vault, swap
  on success.
- **Scope:** `scripts/claude_code_intel_backfill_v2.py`:
  1. Provisions parallel vault at
     `artifacts/knowledge-vaults/claude-code-intelligence-v2/`.
  2. Walks every packet, reuses cached URL fetches when available.
  3. Replays through the v2 ingest path (PR 15 + 16 wired).
  4. Writes a diff summary (entity count, contradictions surfaced).
  5. Atomic swap: rename old → archive, new → canonical.
  6. Triggers brief + capability library rebuild.
- **Risk:** Medium. Bounded LLM cost (~few thousand calls). Risk is mostly
  in the swap logic — needs to be safe to abort mid-way.
- **Tests:** Synthetic packet corpus; assert parallel vault populated
  correctly; assert swap is idempotent.
- **Estimate:** Medium (~400 lines + tests + runbook update).

#### PR 17 — Wire intel_lanes config into existing ingest paths
- **Need:** PR 11 added `intel_lanes.yaml` + loader. Currently `claude_code_intel.py`
  still reads handles from `UA_CLAUDE_CODE_INTEL_X_HANDLES` env var and hard-codes
  the vault slug. Should read from the lane config so the eventual Codex/Gemini
  lane expansion is configuration-only.
- **Scope:** Refactor `ClaudeCodeIntelConfig.from_env` and downstream
  references to vault slug, capability library slug, allowlist (already used
  by research grounding) to read from `get_lane(slug)`.
- **Risk:** Low–medium. Largely mechanical; behavior must be identical for
  the existing `claude-code-intelligence` lane.
- **Tests:** Assert behavior unchanged when reading from default lane;
  assert switching `--lane` argument routes correctly.
- **Estimate:** Medium (~300 lines + tests).

#### PR 13 — Vault lint sweep (monthly, reports only)
- **Need:** Per design §4.3, periodic contradiction detection across
  entity/concept pages. Reports only — does not auto-fix.
- **Scope:** Cron job (monthly) that walks `vault/entities/` and
  `vault/concepts/`, asks an LLM to find contradictions, writes report to
  `vault/lint/contradictions-YYYY-MM-DD.md`. Existing `lint_vault` function
  is the starting point.
- **Risk:** Low — report-only, bounded LLM call.
- **Tests:** Synthetic vault with known contradictions; assert detection.
- **Estimate:** Small (~250 lines + tests).

### Phase D — Cross-pipeline generalization & lifecycle

#### PR 14 — YouTube tutorial endpoint_profile generalization
- **Need:** Per Kevin's question on 2026-05-05: YouTube tutorials about
  Claude/Anthropic features hit the same dual-environment trap. Tutorials
  about Gemini, OpenAI, etc. are fine where they are.
- **Scope:**
  - Generalize `demo_workspace.provision_demo_workspace` to accept
    `endpoint_profile: anthropic_native | gemini_native | openai_native | none`.
  - Update `youtube-tutorial-creation` skill to detect Anthropic-related
    tutorials and route them through the demo workspace pattern.
  - Update `youtube_daily_digest` dispatcher to pass topic hints.
- **Risk:** Medium — touches a separate pipeline and a skill outside the v2
  CSI scope.
- **Tests:** Profile-aware provisioning; routing decision in tutorial skill.
- **Estimate:** Large (~600 lines + tests + skill update).

#### PR 18 — Demo lifecycle / retire policy
- **Need:** Per design doc § 17 deferred questions. A demo built against
  `claude-code@2.1` may be obsolete when `claude-code@3.0` lands. Without
  a re-validation policy, the capability library will drift toward stale
  reference implementations.
- **Scope:**
  - Add `built_against_versions` block to demo `manifest.json`
    (claude-code, claude-agent-sdk, anthropic SDK versions at build time).
  - When PR 6c auto-applies an upgrade for an Anthropic-adjacent package,
    flag any demos whose `built_against_versions` is now stale by N major
    versions (configurable threshold, default = 1 minor or 1 major).
  - Mark stale demos `needs_revalidation: true` on their entity page.
  - Simone's `cody-work-evaluator` (PR 10) learns to re-queue a stale
    demo for re-build, but with `iteration_reason: revalidation` so the
    feedback loop knows it's not a bug fix.
  - Optional: a "demo retired" state for demos whose underlying feature
    was deprecated by Anthropic.
- **Risk:** Low — additive; doesn't touch any existing path until a real
  demo ages out.
- **Tests:** Synthetic demo with known versions; bump matrix; assert
  staleness flag fires correctly; assert re-queue path works.
- **Estimate:** Medium (~350 lines + tests). Cannot meaningfully ship
  until at least 5–10 real demos exist (which means after PR 9/10 land
  and the system has been running for a few weeks). Currently this PR is
  a placeholder so we don't forget it.

---

## Execution sequence (autonomous AI Coder plan)

The AI Coder will work through this list in order, shipping each PR
independently with tests, and pushing to `feature/latest2` as soon as each
PR is green. The ship operator picks up batches via `/ship` whenever
convenient.

```
Phase A (wire v2 ingest):
    PR 15 → PR 16

Phase B (demo orchestration):
    PR 8 → PR 9 → PR 10

Phase C (operations + generalization):
    PR 6c → PR 12 → PR 17 → PR 13

Phase D (cross-pipeline & lifecycle):
    PR 14 → PR 18 (PR 18 is a placeholder until enough demos exist to age)
```

**Parallelism opportunities** (low-risk PRs that could ship out of order):
- PR 6c can ship anywhere after Phase A (it's small and self-contained).
- PR 13 can ship anytime — pure addition, no dependencies.
- PR 17 can ship anytime — refactor only.

But the canonical order above keeps the dependency chain explicit and lets
the operator reason about ship batches predictably.

---

## Operator gates (things AI Coder cannot do alone)

The AI Coder will pause and surface for operator action whenever:

1. **A PR's first end-to-end test on the VPS would burn meaningful Max plan
   quota.** Specifically PR 9 (Cody actually building demos) will need an
   operator-approved test run before it's considered live.
2. **Cron registration changes.** PR 6c potentially adds new cron behavior;
   PR 13 adds a monthly cron. Cron registration touches `cron_service.py`
   and is operationally sensitive.
3. **The PR 12 backfill swap.** The atomic vault rename should be
   operator-approved on first run, even if the script is fully tested.

Each PR's ship handoff will explicitly call out any of these gates.

---

## Known caveats inherited from prior PRs

These are not blockers but should be tracked:

1. **3 Dependabot vulnerabilities on `main`** (1 high, 1 moderate, 1 low) —
   triage separately from this work.
2. **Filename `rolling_14_day_report.{md,json}`** is preserved for dashboard
   back-compat even though v2 default is 28 days. Cleanup deferred to a
   separate PR.
3. **PR 6b actuator runs synchronously** (~10–15 min for cold sync + smokes).
   Future improvement: background it via Task Hub for autonomous use.
4. **Anthropic-native smoke depends on `claude /login` having been done** on
   the VPS. Already true in production (verified by Kevin 2026-05-05). If
   the OAuth session expires, the actuator rolls back any in-flight upgrade
   and emails Kevin with the failure — that's correct behavior.
5. **The CLI-vs-SDK auth distinction** documented in PR 7b applies to all
   future demo work. Category-2 demos (raw Anthropic SDK) need a separate
   `ANTHROPIC_API_KEY` from `console.anthropic.com`. Not provisioned yet.

---

## Definition of done for v2

Mapped from the original design doc § 19 "Success criteria for v1":

1. ✅ All 12 already-shipped PRs in production.
2. ⬜ All 11 remaining PRs in production (PR 18 is a placeholder; can ship later when demos age).
3. ⬜ A new ClaudeDevs tweet about a real Anthropic feature flows through Phase 1 within one cron tick, producing a vault entity page that cites the official docs in full. **(Requires PR 15 + PR 16.)**
4. ⬜ Simone picks up that entity page on her next heartbeat tick and produces a demo workspace with a real ACCEPTANCE contract. **(Requires PR 8.)**
5. ⬜ Cody builds the demo in `/opt/ua_demos/<demo-id>/`, runs it against real Anthropic endpoints (verified via `manifest.json.endpoint_hit`), and produces working output. **(Requires PR 9.)**
6. ⬜ Simone judges the demo, links it from the vault entity page, and marks it `demo_built`. **(Requires PR 10.)**
7. ⬜ The next 28-day brief surfaces this as a new capability in the "What we built" section. **(Already wired by PR 4 + PR 5; will fire automatically once 3-6 land.)**
8. ⬜ The capability library's relevant bundle includes runnable code derived from the demo. **(Same — already wired; fires after 3-6.)**
9. ⬜ A Phase 0 release announcement triggers an SDK upgrade with smoke tests passing against both environments and an email to Kevin documenting the bump. **(Requires PR 6c.)**
10. ⬜ Backfill of historical packets (one-time) produces a coherent vault that's materially richer than the v1-archive. **(Requires PR 12.)**

When criteria 1, 3–6, 7–8, 9, and 10 are all checked, the v2 system is
meeting its design intent end-to-end. Criterion 2 is the implementation
bookkeeping; criteria 3–10 are the user-visible wins.

---

## How to update this doc

After every PR ship, the AI Coder updates:
1. The "What's already shipped" table — moves the PR row in, adds commit SHA.
2. The "What's left" section — strikes through completed PRs.
3. The "Known caveats" section — adds anything new the PR surfaced.

This keeps the plan synchronized with reality so the next AI Coder session
or operator can pick up cleanly.
