# Hermes Continued — Handoff Brief for the Next Coding Agent

**Date:** 2026-05-11
**Session leaving off:** Cloud-sandbox Claude (no AgentMail / no VPS / no `just dev` access)
**Branch this hands off:** `claude/hermes-phase-b2-dashboard` @ `0c7df57` (already pushed)
**Primary plan doc:** [`docs/reports/hermes-adaptation-phased-plan-2026-05-10.md`](./hermes-adaptation-phased-plan-2026-05-10.md) — six-phase plan A-F
**Investigation report this implements:** [`docs/reports/hermes-kanban-tenacity-comparison-2026-05-10.md`](./hermes-kanban-tenacity-comparison-2026-05-10.md)

> **Read this whole document before you touch any code.** It's the consolidated context you need so you don't re-litigate decisions or accidentally undo recent shipping. It assumes you've also done a baseline read of `CLAUDE.md` and `docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md`.

---

## 1. Mission

Carry the Hermes Kanban + Tenacity adaptation forward through phases B.2 → C → D → E → F per the plan doc. **Phase B.1 is shipped to production. Phase B.2 is WIP on the named branch and needs ~6 unit tests + verification + amend + PR to merge.** Phases C-F are not yet started.

The original investigation looked at NousResearch Hermes Agent v0.13.0's Kanban + Tenacity release and identified what UA's Task Hub could selectively adopt — per-task retry budgets, stale-assignment release, operator unstick verbs, an attempt-history table, observability for owned subprocesses, and a Cody-on-Anthropic toggle. Phase A landed the foundation, Phase B is the operator unstick verbs, Phase C unblocks Atlas, Phase D adds attempt history that unlocks Simone-callable verbs, Phase E is the Cody Anthropic toggle, Phase F closes the owned-subprocess observability gap.

---

## 2. What's already shipped to `main` (production)

All three shipped via the recovery PR **#198** (merged 2026-05-11). Don't re-do these.

| Phase | What | Files | PR |
|---|---|---|---|
| **A.1** | Per-task `max_retries` override + `_resolve_effective_max_retries` resolution order (task → dispatcher fallback) | `src/universal_agent/task_hub.py` (schema column + helper + finalize_assignments wiring) | #198 (originally #193) |
| **A.2** | `release_stale_assignments` wired into `dispatch_sweep` top-of-tick, with caller-exclusion set for `provider_session_id` | `src/universal_agent/services/dispatch_service.py` | #198 (originally #194) |
| **B.1** | Four operator unstick verbs (`rehydrate` / `re_evaluate` / `redirect_to` / `request_revision`), plus `_rehydrate_task` and `_summarize_prior_assignments` helpers | `src/universal_agent/task_hub.py` § `perform_task_action` and helpers | #198 (originally #195) |
| **Infra** | `pr-auto-merge.yml` workflow — auto-enables auto-merge on `claude/*` PRs to `main` | `.github/workflows/pr-auto-merge.yml` | #198 |
| **Docs** | Master Task Hub ref § 13.0 / 13.0.1 (full VALID_ACTIONS table + unstick verbs deep-dive), plus the two `docs/reports/hermes-*.md` reports | various | #198 |

The full canonical action reference + the Mermaid state diagram showing the rehydrate transitions is at [`docs/03_Operations/107_Task_Hub_And_Multi_Channel_Execution_Master_Reference_2026-03-31.md` § 13.0](../03_Operations/107_Task_Hub_And_Multi_Channel_Execution_Master_Reference_2026-03-31.md). Read that section once so you understand the data shapes.

---

## 3. What's in flight (B.2) — start here

**Branch:** `claude/hermes-phase-b2-dashboard` at commit `0c7df57` (rebased onto current `origin/main`, already pushed).

**What the WIP commit contains:**

* **Backend (`src/universal_agent/gateway_server.py`, +57 LOC):** new GET endpoint `/api/v1/dashboard/todolist/tasks/{task_id}/failure-context` immediately after `dashboard_todolist_get_subtasks` (around L22518). Returns a JSON payload with `last_disposition`, `last_disposition_reason`, retry counters + limits, `last_side_effect_summary`, `re_evaluation_context`, `revision_round`, rehydrate audit fields, `max_retries`, and a `prior_assignments` list. Uses `task_hub._summarize_prior_assignments(conn, task_id=..., limit=5)`.
* **Frontend (`web-ui/app/dashboard/todolist/page.tsx`, +193 / -3 LOC):** `FailureContext` type, `failureContext` + `failureContextLoading` state, `handleFetchFailureContext` callback, `handleTaskAction` augmented to accept optional `{ reason, note }` extras, `handleUnstickVerb` dispatcher that uses `window.prompt` to collect a target agent for `redirect_to` and feedback text for `request_revision`, drawer block that renders the failure context block + 4 unstick buttons (`Rehydrate`, `Re-evaluate`, `Redirect to`, `Request revision`).

**What's NOT in the WIP and you need to add:**

1. **Unit tests** at `tests/unit/test_dashboard_failure_context_endpoint.py` — these 6 cases:
   1. Returns 404 for a non-existent task.
   2. Returns failure context for a fresh `open` task (all counters zero, `last_disposition_reason` empty, `prior_assignments` empty list).
   3. Returns populated payload for a task wedged in `needs_review` (counters > 0, `last_disposition_reason` populated, `last_side_effect_summary` populated). Mirror the fixture pattern from `tests/unit/test_task_hub_unstick_verbs.py::_seed_wedged_task`.
   4. Returns `re_evaluation_context` block when one was previously attached (simulate by calling `task_hub.perform_task_action(action="re_evaluate")` on a seeded task, then hitting the endpoint).
   5. Returns `revision_round > 0` when a previous `request_revision` ran.
   6. `prior_assignments` list contains rows with the expected keys (`assignment_id`, `agent_id`, `state`, `started_at`, `ended_at`, `result_summary`) and is ordered newest-first.

   Use FastAPI's `TestClient` — there's no project-wide custom pattern, just import `from fastapi.testclient import TestClient` and the app from `universal_agent.gateway_server`. Check `tests/unit/test_dashboard_todolist_*.py` (if any exist) for the project's preferred fixture style — grep first.

2. **Documentation Status entry.** Add a short "B.2 shipped" addition to `docs/Documentation_Status.md` and bump the Implementation Status section in `docs/reports/hermes-adaptation-phased-plan-2026-05-10.md` § Phase B.

3. **Verification gates** (matches CI exactly):
   ```
   python -m py_compile src/universal_agent/gateway_server.py tests/unit/test_dashboard_failure_context_endpoint.py
   uv run --offline ruff check --select E9,F --ignore E402,F401,F541,F811,F841 --no-cache <changed files>
   uv run --offline pytest tests/unit -x -q --no-header
   ```
   Expect ~2762 passing after your 6 new tests land (2756 currently + 6 new).

4. **Local UI smoke** via `just dev` on Kevin's desktop. Boot the stack, navigate to `http://localhost:3000/dashboard/todolist`, seed a needs_review task in the dev SQLite (or use one that Simone's heartbeat naturally produces), click into it, verify the drawer renders the failure-context block + 4 buttons, click Rehydrate and confirm the task reopens.

5. **Amend the WIP commit** with the new tests + doc updates, force-push (it's a `claude/*` branch, force-push is fine), open PR to `main`. **`pr-auto-merge.yml` will auto-enable auto-merge** because the head branch matches `claude/*`. The PR will land on its own once CI is green and Kevin clicks merge (or with the right branch-protection toggle, fully automatically).

---

## 4. Outstanding cron-error triage (BLOCKING before further B.2 work)

Earlier in this session Kevin flagged "a bunch of cron run errors" in Simone's mailbox over the past hour. **The sandbox couldn't reach the AgentMail MCP**, so the triage wasn't done. **Your first action on resume is to triage those.** Tools you should have available in your new launch (per the start-instructions doc): `mcp__agentmail__list_threads`, `mcp__agentmail__get_thread`. Filter to the past 1-2 hours, look for any cron-error / heartbeat-failure / dispatch-failure threads from Simone. Decision tree:

* **If errors are pre-existing infra noise** (e.g. flaky external API, rate-limit hit, transient ZAI peak-time slowdown) — note them in the chat to Kevin and continue B.2.
* **If errors are caused by something B.1 shipped** (e.g. a regression from the unstick verbs or `release_stale_assignments` wiring) — STOP B.2, open a hotfix PR first, get the regression off prod, then resume.
* **If errors are caused by the local-dev / loop_control gate flips** — surface to Kevin; this is initiative-side, not Hermes-side, so route it to whoever owns that initiative.

The fact that B.1 shipped 2026-05-11 makes regression a real candidate. Don't assume noise.

---

## 5. Outstanding phases C-F (do these in order after B.2 lands)

### Phase C — Atlas-direct-dispatch + Simone awareness

**Goal:** Stop throttling Atlas behind Simone's heartbeat. Add an independent dispatcher that auto-calls `vp_dispatch_mission` for tasks tagged `metadata.preferred_vp = "vp.general.primary"`. Emit a `delegation_fyi` event so Simone retains situational awareness.

**Key files:** new module probably under `services/`, plus a hook into the gateway cron registration. Read `docs/reports/hermes-adaptation-phased-plan-2026-05-10.md` § Phase C in full before you start — there are two v2 corrections you need to internalize about JSON-path semantics (`metadata.preferred_vp` is **top-level**, NOT `metadata.dispatch.preferred_vp`).

**Estimated size:** ~200-300 LOC + tests.

### Phase D — `task_hub_runs` attempt-history table

**Goal:** Add per-attempt durable history. Unlock Simone-callable tool versions of the B.1 verbs (deferred from B.1 because she needs the run history to judge from).

**Key files:** `task_hub.py` schema migration, helpers, `tools/` registrations for the 3 Simone-callable verbs (`task_re_evaluate`, `task_redirect_to`, `task_request_revision`).

**Estimated size:** ~300-400 LOC + schema migration + tests.

### Phase E — Cody Anthropic-vs-ZAI toggle

**Goal:** Let Cody optionally run on Anthropic Max plan instead of ZAI when the demo needs full Claude capability. Today Cody is always ZAI like the rest of UA's autonomous agents.

**Key files:** Cody's four execution paths (VP SDK in-process, VP CLI subprocess, autonomous-mission worktree, demo workspace) each need a per-invocation toggle. The settings.json / env-var injection point for each path differs — read the plan § Phase E carefully.

**Estimated size:** ~150-250 LOC across the four paths.

### Phase F — Owned-subprocess observability

**Goal:** Add PID tracking, exit classification, and protocol-violation detection (clean-exit-but-no-disposition) across the three UA-owned subprocess sites: cron registration, VP CLI client, demo workspace execution.

**Key files:** various — see plan § Phase F.

**Estimated size:** ~200-300 LOC + tests.

---

## 6. Critical context the new agent MUST internalize

### 6.1 Branch model (post-2026-05-10)

* `develop` is **retired**. The chain was `feature/latest2 → develop → main`; it's now **any feature branch → PR → main → Deploy**. Don't try to use `develop`.
* **Tier 1** (Kevin + you in interactive coding): work on `feature/latest2` or any feature branch, commit, push, run `/ship` or open a PR directly. `/ship` is **slim now** — it commits, pushes, opens a PR to `main`, enables auto-merge, exits. It does NOT promote through develop.
* **Tier 2** (autonomous bots): worktree → patch → syntax-check → unit tests → push to `<bot>/<task-id>` → open PR → `pr-auto-merge.yml` auto-enables auto-merge → CI passes → GitHub squash-merges → `deploy.yml` fires from the main push → production.
* PRs from a `claude/*` head branch to `main` get auto-merge enabled by `pr-auto-merge.yml`. **You don't need to manually call `enable_pr_auto_merge`** — the workflow does it on every PR open/reopen/sync.

### 6.2 Local dev (post-2026-05-11)

* Desktop has `just dev` — boots gateway + API + web-ui locally. **Autonomous loops are OFF by default in `UA_RUNTIME_STAGE=development`.** To turn one on, set `UA_DEV_<NAME>_FORCE_ON=1`.
* **`UA_<NAME>_ENABLED=1` is IGNORED in dev** (defensive against Infisical prod-parity injection). Only `UA_DEV_<NAME>_FORCE_ON=1` opts in.
* New helpers: `src/universal_agent/loop_control.py` (`should_run_loop()`, `explain_loop_decision()`), `src/universal_agent/dev_tools/` (CLI: `env-report`, `loop-status <name>`, `cron-list`), `scripts/snapshot_prod_to_dev.py`. Use these to debug dev behavior — don't grep blind.
* Canonical doc: [`docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md`](../06_Deployment_And_Environments/12_Local_Dev_Environment.md). Read it once.

### 6.3 Claude execution profiles

Three distinct profiles run in this repo. Don't conflate them.

| Profile | Where | Model |
|---|---|---|
| Kevin's interactive coding (and you, in the new session) | Any `claude` launch from a shell on desktop or VPS | **Anthropic Max plan** via OAuth |
| UA autonomous loops (heartbeat / cron / Simone / Atlas / Cody today / ClaudeDevs) | UA services on the VPS | **ZAI proxy / GLM models** |
| Demo workspace execution | `/opt/ua_demos/<id>/` on the VPS | **Anthropic Max plan** via OAuth (vanilla project-local settings override the ZAI mapping) |

The Hermes Phase E plan adds a toggle to move Cody from row 2 to row 1 for the cases that need real Claude. Today, only the demo workspaces run on Anthropic; everything else autonomous is ZAI.

Canonical doc: [`docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md`](../06_Deployment_And_Environments/10_Interactive_Coding_Environment.md).

### 6.4 Common mistakes from this session — do NOT repeat

1. **Targeting `feature/latest2` instead of `main`.** The earlier round of Hermes PRs (#193-#197) targeted `feature/latest2` against the old branch model. They had to be recovered by cherry-picking the code commits onto a fresh `claude/hermes-recovery-to-main` branch (PR #198) and discarding the wrong-direction `auto-promote-to-prod.yml` workflow.
2. **Building an `auto-promote-to-prod.yml` workflow.** This was a wrong-direction artifact from the old `feature/latest2 → develop → main` model. There is no promote step needed — deploy.yml fires on push to main directly.
3. **Trusting an Explore agent's summary without verifying with `git diff` / `git show`.** An Explore agent during this session hallucinated that my WIP commit contained loop_control refactor edits. It did not — it was a clean +57 LOC pure addition. Verify before planning around an agent's claim.
4. **Assuming AgentMail MCP is available in the sandbox.** It's not loaded by default. Only sessions launched via `scripts/claude_with_mcp_env.sh` (or equivalent wrapper that runs `initialize_runtime_secrets()`) get the MCP tools.

---

## 7. The recovery story (so you don't repeat it)

The Hermes plan was drafted on 2026-05-10 and Phase A.1, A.2, B.1 implementations were drafted that day. They were PR'd to `feature/latest2` and squash-merged there — but `feature/latest2 → main` was no longer the canonical flow (develop had been retired the same day). Five PRs (#193, #194, #195, #196, #197) accumulated on `feature/latest2` without ever reaching `main`. Production sat unchanged.

The recovery: open one PR (#198) targeting `main` directly. Cherry-pick the 3 code commits (A.1, A.2, B.1). Add the corrected `pr-auto-merge.yml` (targeting `main`, NOT feature/latest2). Drop the wrong-direction `auto-promote-to-prod.yml`. Update all the doc references. Merge it. **That's the SHA `e689d2f` you'll see in `git log origin/main`.**

`feature/latest2` is now a stale branch. Don't push to it. Don't target PRs at it. Branch off `origin/main` directly for new work.

---

## 8. Where to find things (file map)

| What | Where |
|---|---|
| Hermes plan (six phases A-F) | `docs/reports/hermes-adaptation-phased-plan-2026-05-10.md` |
| Hermes comparison investigation | `docs/reports/hermes-kanban-tenacity-comparison-2026-05-10.md` |
| Task Hub master reference (action table, lifecycle) | `docs/03_Operations/107_Task_Hub_And_Multi_Channel_Execution_Master_Reference_2026-03-31.md` |
| Branch + deploy model | `docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md` |
| CI/CD pipeline | `docs/deployment/ci_cd_pipeline.md` |
| Local dev environment | `docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md` |
| Claude execution profiles | `docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md` |
| Phase B.1 verbs implementation | `src/universal_agent/task_hub.py` § `perform_task_action`, `_rehydrate_task`, `_summarize_prior_assignments` |
| Phase B.1 verb tests | `tests/unit/test_task_hub_unstick_verbs.py` |
| Phase A.1 retries implementation | `src/universal_agent/task_hub.py` § `_resolve_effective_max_retries` (around L108) |
| Phase A.2 stale-release | `src/universal_agent/services/dispatch_service.py` |
| WIP B.2 endpoint | `src/universal_agent/gateway_server.py` § `dashboard_todolist_get_failure_context` (around L22518) |
| WIP B.2 UI | `web-ui/app/dashboard/todolist/page.tsx` § FailureContext + drawer block |
| Agent PR auto-merge workflow | `.github/workflows/pr-auto-merge.yml` |
| PR validation workflow | `.github/workflows/pr-validate.yml` |
| Deploy workflow | `.github/workflows/deploy.yml` |
| `/ship` slim command | `.claude/commands/ship.md` |

---

## 9. Concrete first-hour checklist for the new agent

1. Read this whole document.
2. Read `docs/reports/hermes-adaptation-phased-plan-2026-05-10.md` § "Implementation Status" + § Phase B.
3. Read `CLAUDE.md` from the start through "Pre-Implementation Reading" at minimum.
4. Verify your environment: launch via the start-instructions doc, then run `git log origin/main --oneline -3` to confirm you're seeing `e689d2f` (the B.1 recovery) and the post-initiative commits.
5. Triage the cron-error mailbox per § 4 above.
6. If no Hermes-induced regression, check out `claude/hermes-phase-b2-dashboard` and resume B.2 per § 3.
7. After B.2 lands, proceed to Phase C.

---

## 10. Out of scope

* Re-architecting the branch model. It's settled. Don't.
* Adding new autonomous loops or cron jobs without env-flag gates. Read `loop_control.py` first; any new loop you add must respect the same OFF-in-dev semantics.
* Touching `feature/latest2`. It's stale; leave it.
* Building any "promote" workflow between branches. `deploy.yml` fires on push to `main`; that's the only promotion that exists now.

---

## 11. Open questions for Kevin (do NOT decide unilaterally)

* **Cron error severity.** Once you triage Simone's mailbox, flag the worst error to Kevin and ask whether to hotfix-first or park-as-followup.
* **B.2 UX details.** The current WIP uses `window.prompt` for `redirect_to` (target agent_id) and `request_revision` (feedback text). This is functional but not pretty. If Kevin wants modal-driven UX, that's a follow-up — don't expand B.2 scope to do it now.
* **Phase D Simone-callable verbs.** Once `task_hub_runs` lands, the three tool versions (`task_re_evaluate`, `task_redirect_to`, `task_request_revision`) need a system-prompt addendum so Simone knows when to use them. The plan doc doesn't fully spec the addendum text. Ask Kevin to draft it before you wire the tools.

---

## 12. Closing note

The Hermes adaptation is a deliberate, phased uplift of UA's existing Task Hub — NOT a port of Hermes. Don't import Hermes code. Don't reshape UA to mimic Hermes architecture. The plan doc is conservative on purpose. When in doubt, prefer the smaller change that fits UA's existing patterns; you can always expand later.
