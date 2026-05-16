# ClaudeDevs Intel v2 — Remaining Work Plan

> **Status:** ✅ **Phases A + B complete in production as of 2026-05-16.** End-to-end loop verified (1 demo attached to `custom-subagents.md` entity page; 4 demo workspaces in `/opt/ua_demos/`). Only Phase C (operations/generalization) + Phase D (cross-pipeline lifecycle) remain — all small, optional, individually scopable. Living document.
> **Last updated:** 2026-05-16 (post-gateway-incident audit: Phase A wiring + Phase B skill invocation + Phase 4 vault-attach all confirmed wired in `claude_code_intel_replay.py:116-148, 864-880` + `memory/HEARTBEAT.md:93-101`)
> **Owner:** AI Coder on `main`, with operator gates for `/ship` cycles
> **Companion:** [`claudedevs_intel_v2_design.md`](claudedevs_intel_v2_design.md) (the original 13-PR design doc)
> **🚨 Read first if returning after a session break:** [`csi_v2_next_session_priorities_2026-05-06.md`](csi_v2_next_session_priorities_2026-05-06.md) — the unambiguous "do this next" list.

## TL;DR — production state on 2026-05-16

What's running in production:

- **Phase A — vault ingest** wired and producing: 64 entity pages in `/opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence/entities/`. Memex pass (PR 15) gated by `UA_CSI_MEMEX_WIRING_ENABLED=1`. Research grounding (PR 16) gated by `UA_CSI_RESEARCH_GROUNDING_WIRING_ENABLED=1`.
- **Phase B — Simone↔Cody demo loop** wired: all 7 skills (`cody-scaffold-builder`, `cody-task-dispatcher`, `cody-implements-from-brief`, `cody-progress-monitor`, `cody-work-evaluator`, `vault-demo-attach`, `project-scaffolder`) on disk AND invoked from `memory/HEARTBEAT.md:85-101`. Helper functions in `src/universal_agent/services/cody_evaluation.py` + `cody_implementation.py` + `cody_dispatch.py` + `cody_scaffold.py`.
- **End-to-end verified once:** `custom-subagents.md` carries a `## Demos` section pointing at `/opt/ua_demos/custom-subagents__demo-1/`. 2 `cody_demo_task` rows in `task_hub_items` are `status=completed`. Producer wiring (5 historical `cody_scaffold_request` rows; 3 ran fully through scaffold→dispatch→build→evaluate→attach).

What's left:

- **Phase C / D** — small, individually scopable, no longer blocking the v2 "Definition of done" criteria 3–8 (those depend on the just-confirmed wiring, which is live).
- **Live observability** — wait for the next organic tier-3 CSI fire (gateway healthy again as of 2026-05-16 16:49 UTC after the orphan-socket incident; PR #297 + PR #299 are the permanent fix).

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

**Production SHA at time of writing:** post-`08c49fa` ship cycle on `main` (5 ship cycles on 2026-05-06 alone).

### Shipped 2026-05-06 (this session, post-shakedown)

| Commit | Subject |
|---|---|
| `cc14d94` | feat(csi): add 22:00 Central poll to ClaudeDevs intel cron (3x daily) |
| `09d7ee2` | fix(csi): enable `catch_up_on_restart` so future deploys backfill missed cron windows |
| `185e552` | fix(csi): `trust_source` bypass for linked-doc fetch + `claude.com` allowlist widening |
| `c312ea8` / `7e2aa71` / `0b22877` | docs: Production Verification Rules in `CLAUDE.md` (3 commits incl. corrections) |
| `f38075d` | docs: Pre-Implementation Reading rules in `CLAUDE.md` |
| `5682fc5` | feat(csi): tier-3 actions enqueue `cody_scaffold_request` instead of direct `claude_code_demo_task` (Phase 2 producer wiring) |
| `6dc6f51` / `20bf032` | YouTube digest proxy retry + stale `require_proxy` test cleanup |

**⚠️ Phase 2 producer was superseded on 2026-05-09 by the operator-gated triage drawer (commit `5a3a936a`).** The auto-queue path from tier-3 candidate → `cody_scaffold_request` is gone. The only path from a tier-3 candidate to Cody is now the operator clicking **Approve** in the Demo Triage drawer on `/dashboard/claude-code-intel`. See [`csi_demo_triage_handoff_2026-05-09.md`](csi_demo_triage_handoff_2026-05-09.md) for the full rationale (the auto-queue was producing too many low-value Cody tasks and starving the human-review feedback loop). Validation that the drawer is operational is in the handoff doc; what was *not* validated until 2026-05-16 was that operators were actually clicking it — see PR for the morning-briefing surfacing fix.

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
| PR 8 — Simone Phase 2 skills | PR 8 (skills) + 2026-05-06 producer **+ 2026-05-09 supersession** | ⚠️ skills shipped earlier; original producer (`5682fc5`) **replaced** by operator-gated triage drawer (`5a3a936a`); **end-to-end verified on 2026-05-16 only after morning-briefing surfacing fix landed** |
| PR 9 — Cody Phase 3 skill | PR 9 | ⬜ pending |
| PR 10 — Simone Phase 4 skills | PR 10 (skills) + 2026-05-16 vault-attach wiring | ⚠️ skills shipped earlier; vault-attach heartbeat directive + backfill script wired on 2026-05-16 |
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

### Phase A — Wire the v2 ingest pipeline ✅ COMPLETE

Both wiring PRs shipped and live. Phase A latent integration gaps from PRs 2, 3, 11 are closed. The vault is producing v2-style content (64 entity pages in production as of 2026-05-16).

#### PR 15 — Memex wiring into ClaudeDevs replay path ✅ SHIPPED
- **Wired at:** `src/universal_agent/services/claude_code_intel_replay.py:864-880` calls `apply_memex_pass()` (lines 598-748) after `wiki_ingest_external_source`. The pass uses the LLM-driven extractor (`csi_intelligence_pass.analyze_action` → GLM-5.1) feeding `csi_intelligence_persistence.apply_vault_delta_to_vault` which routes CREATE/EXTEND/REVISE through `memex_apply_action`. Per-action errors surface as `action="ERROR"` records and don't abort the replay.
- **Toggle:** `UA_CSI_MEMEX_WIRING_ENABLED` (defaults to ON; emergency off switch).
- **Verification:** 64 entity pages live in `/opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence/entities/`.

#### PR 16 — Research grounding wiring into Phase 1 ingest ✅ SHIPPED
- **Wired at:** `src/universal_agent/services/claude_code_intel_replay.py:116-148` calls `apply_research_grounding_pass()` (lines 435-549) between linked-source expansion and vault ingest, so the Memex pass sees grounded sources alongside originally-linked ones. Grounded entries flow into `linked_source_entries` via list-merge (line 148) and are counted separately in `linked_source_count` semantics for stability.
- **Toggle:** `UA_CSI_RESEARCH_GROUNDING_WIRING_ENABLED` (defaults to ON; emergency off switch).
- **Cost guardrail:** Trigger logic enforced at `research_grounding.build_research_request` keeps the tier ≥ 2 gate strict.

### Phase B — Demo orchestration (the Simone↔Cody loop) ✅ COMPLETE

All three PRs shipped and live. End-to-end verified once on `custom-subagents.md` (workspace at `/opt/ua_demos/custom-subagents__demo-1/`, `## Demos` section on the entity page). 2 `cody_demo_task` rows completed historically; 3 of 5 `cody_scaffold_request` rows ran fully through the loop. Waiting on next organic tier-3 post for fresh live verification.

#### PR 8 — Simone Phase 2 skills (`cody-scaffold-builder`, `cody-task-dispatcher`) + producer wiring (`5682fc5`, superseded by `5a3a936a`)
- **Skills (originally PR 8):** ✅ shipped — both `cody-scaffold-builder` and `cody-task-dispatcher` are on disk and idempotent.
- **Producer wiring (added 2026-05-06, replaced 2026-05-09):** initial implementation in `5682fc5` made `claude_code_intel.queue_follow_up_tasks` write `cody_scaffold_request` Task Hub rows directly for every tier-3 action. **Replaced on 2026-05-09 by the operator-gated triage drawer (`5a3a936a`)** — see [`csi_demo_triage_handoff_2026-05-09.md`](csi_demo_triage_handoff_2026-05-09.md). The auto-queue was producing more candidates than the human review loop could absorb; the drawer at `/dashboard/claude-code-intel` is now the single gated path.
- **Surfacing the queue depth (added 2026-05-16):** the morning briefing now embeds a "Claude Code Demo Triage" block listing pending count + top-ranked candidates whenever `pending > 0`. Killable via `UA_TRIAGE_BRIEFING_BLOCK_ENABLED=0`. Code: `src/universal_agent/scripts/briefings_agent.py:_get_triage_block_or_empty`. This closed the gap where 12+ tier-3 candidates piled up unseen for 6 days because the drawer had no proactive notification.
- **Emergency fallback:** `UA_CSI_DIRECT_DEMO_FALLBACK=1` re-enables the legacy direct-to-Cody enqueue if the operator-gated path misbehaves. Default off; should remain off.
- **End-to-end verification (2026-05-16):** the loop is now provable in production. Operator approval in the drawer → `cody_scaffold_request` row → Simone Phase 2 claim → workspace scaffolded → Cody build → `/opt/ua_demos/<id>/manifest.json` written → Simone Phase 4 evaluator → `vault-demo-attach` (via the new HEARTBEAT directive — see PR 10 below).

#### PR 9 — Cody Phase 3 skill (`cody-implements-from-brief`) ✅ SHIPPED
- **Skill on disk:** `.claude/skills/cody-implements-from-brief/SKILL.md`.
- **Helper module:** `src/universal_agent/services/cody_implementation.py` exports `read_manifest()`, scaffolds workspace runs, captures `manifest.json` + `run_output.txt` + `BUILD_NOTES.md`.
- **Cody dispatcher:** `cody_dispatch.py:155` `reissue_cody_demo_task_with_feedback()` handles the iterate path from Simone's evaluator.
- **End-to-end exercised:** `/opt/ua_demos/custom-subagents__demo-1/` has a complete `manifest.json` with `endpoint_hit` matching `endpoint_required`. 4 demo workspaces live in `/opt/ua_demos/` total.

#### PR 10 — Simone Phase 4 skills (`cody-progress-monitor`, `cody-work-evaluator`, `vault-demo-attach`)
- **Skills (originally PR 10):** ✅ shipped — all three SKILL.md files are on disk under `.claude/skills/`, and the Python helpers (`monitor_demo_tasks`, `evaluate_demo`, `attach_demo_to_vault_entity`, `complete_demo_task`, `defer_demo_task`, `detach_demo_from_vault_entity`) all exist in `src/universal_agent/services/cody_evaluation.py` with full unit-test coverage.
- **Heartbeat wiring (added 2026-05-16):** `memory/HEARTBEAT.md` now carries a `## CSI demo-task review → vault attach (Simone owns)` directive. Each cycle Simone scans `monitor_demo_tasks(conn)`, picks the oldest `pending_review` cody_demo_task, runs `cody-work-evaluator`, and on a pass verdict invokes `vault-demo-attach` to append the `## Demos` bullet to `artifacts/knowledge-vaults/claude-code-intelligence/entities/<entity_slug>.md`. Concurrency capped at one demo per cycle.
- **Legacy demos backfilled:** new one-shot script `src/universal_agent/scripts/backfill_demo_attachments.py` walks `/opt/ua_demos/` and runs `attach_demo_to_vault_entity` once per workspace. Used to retroactively link the three orphaned pre-2026-05-09 demos (custom-subagents, webhooks; e3rneinuzx requires `--mapping`).
- **Verification (per `CLAUDE.md` Production Verification Rule §1):** `grep -n vault-demo-attach memory/HEARTBEAT.md` returns ≥1; `grep -l "## Demos" /opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence/entities/*.md` should be ≥1 after running the backfill on the VPS.

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

#### PR 14 — YouTube demo unification (next-phase initiative)

**Scope expanded 2026-05-16.** What the original design framed as "YouTube tutorial endpoint_profile generalization" is in fact the trigger for a broader consolidation: the CSI demo workspace pattern (BRIEF/ACCEPTANCE/manifest + evaluation + iteration + vault-attach) is content-agnostic and would substantially improve the YouTube tutorial pipeline. The runnable-demo half of YouTube tutorials should flow through the same Simone→Cody loop as CSI demos; the YouTube skill should keep producing `CONCEPT.md` as a standalone tutorial doc.

**Trigger condition (operator preference 2026-05-16):** Defer until CSI v2 has run live end-to-end for a meaningful period — minimum ≥5 organic tier-3 demo-triage approvals flowing scaffold→build→evaluate→attach without operator intervention.

**Full plan + reasoning + cost breakdown:** [`youtube_demo_unification_plan.md`](youtube_demo_unification_plan.md). Sub-PRs PR 14a (generalize `provision_demo_workspace`), PR 14b (`youtube_demo_request` Task Hub plumbing), PR 14c (producer wiring in `youtube-tutorial-creation`), PR 14d (optional `youtube-tutorials` vault). Combined ~560 lines + tests.

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
2. 🟡 Phases A + B (PRs 15, 16, 9, 10) shipped + invocation wired. Phase C/D remaining: PR 6c, PR 12 (operator-parked), PR 13, PR 17, PR 14, PR 18.
3. ✅ **Verified historically**: tier-3 ClaudeDevs posts flowed through Phase 1, produced vault entity pages citing linked docs. 5 `cody_scaffold_request` rows in `task_hub_items` (2026-05-06 through 2026-05-09). 64 entity pages live in production vault. Waiting on next organic fire for fresh confirmation now that gateway is healthy again (post-2026-05-16 incident).
4. ✅ **Verified historically**: 2 `cody_demo_task` rows are `status=completed` — Simone scaffolded workspaces with ACCEPTANCE/BRIEF/business_relevance from real cody_scaffold_request approvals.
5. ✅ **Verified historically**: 4 demo workspaces in `/opt/ua_demos/` carry `manifest.json` with `endpoint_hit`. `custom-subagents__demo-1` has a complete clean build.
6. ✅ **Verified historically**: `custom-subagents.md` entity page has a `## Demos` section pointing at the workspace. vault-demo-attach has fired in production.
7. 🟡 Will fire automatically once new organic tier-3 demos accumulate; existing successful demo is the first qualifying input.
8. 🟡 Same — wired upstream; fires after demos accumulate.
9. ⬜ A Phase 0 release announcement triggers an SDK upgrade with smoke tests passing against both environments and an email to Kevin documenting the bump. **(Requires PR 6c.)**
10. ⬜ Backfill of historical packets (one-time) produces a coherent vault that's materially richer than the v1-archive. **(Requires PR 12 — operator-parked.)**

**v2 design intent is end-to-end functional in production as of 2026-05-16.** Criteria 3–6 confirmed by inspection; criteria 7–8 are downstream auto-wired (will accumulate); criterion 9 is the only meaningfully-pending feature (PR 6c). Criterion 10 (PR 12 backfill) is operator-parked per the post-incident discussion.

---

## How to update this doc

After every PR ship, the AI Coder updates:
1. The "What's already shipped" table — moves the PR row in, adds commit SHA.
2. The "What's left" section — strikes through completed PRs.
3. The "Known caveats" section — adds anything new the PR surfaced.

This keeps the plan synchronized with reality so the next AI Coder session
or operator can pick up cleanly.
