# Hermes Adaptation Phased Plan

**Date:** 2026-05-10
**Branch:** `claude/hermes-agent-investigation-GfvTQ`
**Companion docs:** [`hermes-kanban-tenacity-comparison-2026-05-10.md`](hermes-kanban-tenacity-comparison-2026-05-10.md) — full investigation, sections 1–14
**Status:** Phased implementation plan distilled from Section 14 of the comparison report. No code changes yet. Each phase is sized to ship as one or two PRs.

> **Plan history:**
> * **v1 (2026-05-10):** initial plan from Section 14 discussion.
> * **v2 (2026-05-10):** corrections after deeper 1M-context code reads. Affected phases (C, E, F) had incorrect code anchors or scope assumptions. Each correction marked `⚠️ v2 correction`.
> * **v3 (2026-05-10):** v2 introduced THREE new errors of its own (caught on third verification pass):
>     1. **Phase C JSON path was wrong** — `metadata.preferred_vp` is the actual storage location set by `proactive_convergence.py:562, 646`, NOT `metadata.dispatch.preferred_vp` as v2 wrote.
>     2. **Phase E "external VP runtime" claim was FALSE** — VP runtime IS in this repo at `vp/worker_loop.py` (937 LOC) with three execution modes (sdk/cli/dag). The Cody Anthropic toggle is fully implementable here without external coordination.
>     3. **Phase F scope-narrowing was too aggressive** — VP CLI client at `vp/clients/claude_cli_client.py:191` IS a UA-owned subprocess (we have the PID). Already has timeout (line 254-266) and SIGTERM/SIGKILL (`_kill_process` at line 529). What's missing is unified observability, not new kill machinery.
>
>     v3 also captures Kevin's correct observation that Cody operates across MULTIPLE environments (VP SDK in-process, VP CLI subprocess, autonomous-mission worktree off `feature/latest2`, demo workspace at `/opt/ua_demos/`). Each Cody-execution-mode correction marked `⚠️ v3 correction`. v2 markers preserved alongside for the historical reasoning trail.

> **Read order:** Comparison report §14 → this plan. The comparison report is the WHY; this plan is the WHAT and the WHEN.

---

## 🚧 Implementation Status (live — updated 2026-05-11)

**Use this section as the session-resumption anchor.** A future Claude session compacted from this conversation should read this section first to know where we are without re-reading the entire conversation.

### Shipped phases

| Phase | PR | Merge SHA | Status |
|---|---|---|---|
| **A.1** — per-task `max_retries` override | [#193](https://github.com/Kjdragan/universal_agent/pull/193) | `c6fe1dd` | ✅ shipped to `main` via recovery PR [#198](https://github.com/Kjdragan/universal_agent/pull/198) 2026-05-11 |
| **A.2** — wire `release_stale_assignments` into `dispatch_sweep` | [#194](https://github.com/Kjdragan/universal_agent/pull/194) | `aaf773a` | ✅ shipped to `main` via recovery PR [#198](https://github.com/Kjdragan/universal_agent/pull/198) 2026-05-11 |
| **B.1** — operator unstick verbs (`rehydrate` / `re_evaluate` / `redirect_to` / `request_revision`) | [#195](https://github.com/Kjdragan/universal_agent/pull/195) | shipped via [#198](https://github.com/Kjdragan/universal_agent/pull/198) | ✅ shipped to `main` 2026-05-11 |
| **B.2** — dashboard failure-context endpoint + drawer UI + 4 unstick buttons | [#220](https://github.com/Kjdragan/universal_agent/pull/220) | `c1c121e5` | ✅ shipped to `main` 2026-05-11 |
| **C** — Atlas-direct-dispatch + Simone awareness (independent cron-registered dispatcher) | [#221](https://github.com/Kjdragan/universal_agent/pull/221) | — | ✅ scaffolded 2026-05-11; default OFF via `UA_ATLAS_DIRECT_DISPATCH_ENABLED=0`; operator flips on after dry-run |
| **D.1** — `task_hub_runs` attempt-history table + claim/finalize wiring | [#222](https://github.com/Kjdragan/universal_agent/pull/222) | `e00842d2` | ✅ shipped to `main` 2026-05-11; additive — D.2 (prompt/UI consumers) follows |
| **D.2** — Phase B `re_evaluate` verb + dashboard drawer consume `task_hub_runs` | [#228](https://github.com/Kjdragan/universal_agent/pull/228) | `9866f18d` | ✅ shipped to `main` 2026-05-11; 3 new unit tests on top of D.1 |
| **E.1 + E.2.a + E.3** — Cody Anthropic/ZAI toggle (CLI path) | — | — | 🟢 in flight 2026-05-11 (this branch); per-task `cody_mode` column, `services/cody_mode.resolve_cody_mode`, CLI `_build_cli_env(cody_mode=...)` strips ANTHROPIC_* + forces agent-teams, VPAgentHealthTile surfaces active anthropic missions. 13 new unit tests. **E.2.b SDK in-process adapter routing deferred** — see § Phase E follow-ups below. |

> **Operator-supporting interludes (2026-05-11):** PR #218 added a minimum-interval guard on the cron `every_seconds` create path. PR #219 added a periodic `_vp_stale_reconcile_loop`. Both close real operator-burden vectors orthogonal to the lettered phases.

### Phase B.1 — SHIPPED via #198

* **Branch:** `claude/hermes-phase-b1-unstick-verbs` (created off `origin/feature/latest2`, no commits yet at time of writing).
* **Confirmed defaults from Kevin (2026-05-11 sync):**
  - **Verb caller scope:** Operator-only via dashboard initially. Simone-callable tool surface deferred to Phase D once `task_hub_runs` lands and gives her rich failure context to judge from.
  - **PR split:** Two PRs — B.1 (verbs in `task_hub.py` + tests), then B.2 (dashboard drawer + API surface).
* **Files to touch (all anchored against post-A.2 `feature/latest2`):**
  - `src/universal_agent/task_hub.py:33` — extend `VALID_ACTIONS` from 10 to 14 entries: add `"rehydrate"`, `"re_evaluate"`, `"redirect_to"`, `"request_revision"`.
  - `src/universal_agent/task_hub.py:4071` — `perform_task_action` — add four new `elif action_norm == ...` handlers.
  - `src/universal_agent/task_hub.py:3571` — existing `add_comment` helper — reuse for `request_revision`.
  - `tests/unit/test_task_hub_unstick_verbs.py` (new) — unit tests for all 4 verbs.
* **Action behaviors (per the §B design above):**

| Action | What it does |
|---|---|
| `rehydrate` | Status `needs_review|blocked` → `open`; reset `metadata.dispatch.heartbeat_retry_count`, `todo_retry_count`, `last_disposition_reason`; preserve task body/history. Clean restart only — no extra context attached. |
| `re_evaluate` | Same as `rehydrate` PLUS attach a structured "failure context" block to `metadata.dispatch.re_evaluation_context` with: last error, retry count, side-effect evidence, prior assignments summary. Simone's prompt assembler reads this block on next claim and surfaces it as an addendum so she judges from evidence instead of from the task title. |
| `redirect_to(agent_id=...)` | Same as `rehydrate` PLUS set `metadata.preferred_vp = <agent_id>` (top-level field — confirmed v3 path that Phase C's Atlas-direct-dispatch sweep reads). |
| `request_revision(feedback=...)` | Same as `rehydrate` PLUS append a comment via `add_comment(author="operator-review", content=feedback)`; increment `metadata.dispatch.revision_round` counter; bump `tasks.max_retries` by 1 to absorb the revision attempt without immediately re-tripping the budget. |

* **Helper to factor:** `_rehydrate_task(conn, task_id, *, agent_id, reason)` consolidates the common state-mutation core (status flip + counter resets + clearing `last_disposition_reason`). The four action handlers wrap it with their action-specific extras.

* **Test plan (target ~10–12 tests):**
  - rehydrate from `needs_review` clears `heartbeat_retry_count`, `todo_retry_count`, `last_disposition_reason`; status flips to `open`; `agent_ready` preserved.
  - rehydrate from `blocked` works the same.
  - rehydrate from terminal status (`completed`, `parked`, `cancelled`) raises or no-ops (decide which — defensive).
  - re_evaluate attaches `metadata.dispatch.re_evaluation_context` with the four expected fields populated.
  - redirect_to(agent_id="vp.general.primary") sets `metadata.preferred_vp` correctly + Atlas-direct sweep eligibility check (already exists from A.2 — verify integration).
  - request_revision appends comment + bumps revision_round + bumps max_retries (or sets it to default+1 if NULL).
  - All four actions emit appropriate evaluation rows in `task_hub_evaluations`.
  - Verify the post-rehydrate task is now claim-eligible (eligibility gates from A.1's reads of `task_hub.py:1388-1418` no longer trip after counter reset).

### Phases not yet started

C, D, E, F as documented below. **Important:** Phase D (`task_hub_runs` table) is the prerequisite for adding Simone-callable tool versions of B's verbs. Phase D should land before any Simone-side B follow-up.

### How to resume after compaction

1. Read this status section + §B design below for the full B.1 spec.
2. `git fetch origin feature/latest2 && git checkout claude/hermes-phase-b1-unstick-verbs` (branch already exists locally and on origin).
3. Implement per the file anchors above.
4. Verification gates (must all pass before commit):
   - `python -m py_compile <changed .py files>`
   - `uv run --offline ruff check --select E9,F --ignore E402,F401,F541,F811,F841 --no-cache <changed files>` (matches CI exactly)
   - `uv run --offline pytest tests/unit -x -q --no-header` (matches CI exactly; full suite, ~150s)
5. Commit using HEREDOC for the message; signing helper requires the working tree to be at `/home/user/universal_agent` (not `/tmp` or other paths). If on a wrong path, switch the main checkout's branch in-place, `git apply` the staged patch, commit, push, then switch back. Pattern proven on A.1 + A.2.
6. Push to `claude/hermes-phase-b1-unstick-verbs`, open PR via `mcp__github__create_pull_request` targeting `feature/latest2`.

### Recurring lessons learned this session (durable)

* **Three rounds of plan corrections happened (v1 → v2 → v3).** Pattern was tracing call chains halfway and stopping. Anti-pattern fix: when implementing each phase, re-verify the file:line anchors with direct reads before writing code; don't trust the plan's anchors blindly.
* **Signing helper allowlists `/home/user/universal_agent` only.** Worktrees in `/tmp` or other paths fail with "missing source" / "Signing failed". Workaround: branch-switch the main checkout in-place, apply patch, commit, switch back. Proven for A.1 + A.2.
* **Auto-generated `MEMORY.md` / `memory/index.json` / `memory/<date>.md` artifacts** accumulate from session-start hooks. Don't commit them to PR branches — `git stash push --include-untracked` before branch-switching, commit them only on the long-lived investigation branch.
* **CI pr-validate runs `pytest tests/unit -x -q --no-header`** on the FULL unit suite. Locally reproduce with the exact same flags before committing. The first CI failure of A.2 was an infra flake (post-checkout git error 128 + "page loading error"); retriggering via a small docstring commit succeeded.
* **`metadata.preferred_vp` is at the TOP level of metadata, NOT under `metadata.dispatch.preferred_vp`.** Producers like `services/proactive_convergence.py:562, 646` set it that way; `queue_proactive_task` passes the dict through unchanged. Phase C's filter has to read `json_extract(metadata_json, '$.preferred_vp')` (corrected in v3).

---

## Phasing principle

Each phase has these properties:

* **Independently shippable.** A phase can land alone and improve the system; later phases enrich it but do not block its value.
* **One or two PRs each.** No phase is a multi-PR mega-feature.
* **Concrete acceptance criteria.** Each phase declares "this is how we know it's done," verifiable on the running system.
* **Code anchors.** Each phase points to the existing files / line numbers it touches.
* **Default decisions called out.** Where a design choice was made by default rather than from explicit Kevin direction, marked `🎯 default — confirm if you disagree`.

Dependency graph (read top-to-bottom; arrow means "should land before"):

```
Phase A (max_retries column + stale-sweep wiring)         ← independent foundation
   ↓
Phase B (unstick Simone — rehydrate / re-evaluate / redirect / request_revision)
   ↓
Phase C (Atlas-direct-dispatch + delegation_fyi awareness)
   ↓
Phase D (task_runs attempt history + Phase B verbs consume it)
   ↓
Phase E (Cody Anthropic toggle, preserves internal concurrency)   ← can land in parallel with B/C/D
   ↓
Phase F (owned-worker reliability for Cody / ToDo dispatch / cron) ← gated on E to verify Anthropic-mode interplay
```

---

## Phase A — Foundation + quick wins

**Goal:** Ship two small additive changes that pay back immediately and lay groundwork for later phases.

### A.1 — Per-task `max_retries` override

**What:** Nullable `max_retries INTEGER` column on `task_hub_items`. Resolution order in `finalize_assignments`: per-task value → caller-supplied → env-var default. Surface on `upsert_item` API and dashboard task-create form.

**Code anchors:**
* `src/universal_agent/task_hub.py:199-332` — schema (add column + migration)
* `src/universal_agent/task_hub.py:2977-3169` — heartbeat / todo retry policies (consume override)
* `src/universal_agent/task_hub.py` `upsert_item` (accept new field)

**Acceptance:**
* New column present after migration; existing rows have `max_retries = NULL`.
* A task created with `max_retries = 1` blocks on the first failure; default-bucket task still uses env var (`UA_TASK_HUB_HEARTBEAT_MAX_RETRIES = 3`).
* Dashboard task-create form lets the operator set max_retries; existing tasks unchanged.

**Estimated size:** ~50–80 LOC + 1 migration + 3 unit tests.

### A.2 — Wire `release_stale_assignments` into `dispatch_sweep`

**What:** Call `task_hub.release_stale_assignments(prefix=("heartbeat:", "todo:"), stale_after_seconds=...)` at the top of `dispatch_sweep` (the heartbeat-driven entry point — NOT the dashboard-click entry points). Pass `running_session_ids` filter so we don't release a session that's actually busy. Make `stale_after_seconds` configurable via env var (`UA_DISPATCH_STALE_AFTER_SECONDS`, default 1800).

**Why only `dispatch_sweep`:** `services/dispatch_service.py` has FOUR entry points: `dispatch_immediate` (dashboard "Start Now"), `dispatch_on_approval` (dashboard "Approve"), `dispatch_scheduled_due` (timer), `dispatch_sweep` (heartbeat). The dashboard-click handlers respond to user input — they don't need to scan for stale assignments inline. The heartbeat sweep is the only one that runs on a regular cadence regardless of input, so it's the right place to absorb the per-tick stale-sweep cost.

**Code anchors:**
* `src/universal_agent/services/dispatch_service.py:190-223` — `dispatch_sweep` (add the call here)
* `src/universal_agent/services/dispatch_service.py:57-95` — `dispatch_immediate` (NO change — explicitly out of scope)
* `src/universal_agent/services/dispatch_service.py:102-142` — `dispatch_on_approval` (NO change — explicitly out of scope)
* `src/universal_agent/services/dispatch_service.py:149-183` — `dispatch_scheduled_due` (NO change — explicitly out of scope; timer cadence is sparse and this path is already gated by `due_at`)
* `src/universal_agent/task_hub.py:3259-3308` — existing `release_stale_assignments` (no change)
* `src/universal_agent/heartbeat_service.py` — source of `running_session_ids`

**Acceptance:**
* Stale assignment (started_at older than `stale_after_seconds` AND session not in `running_session_ids`) gets marked `state='failed'` on next `dispatch_sweep`.
* The associated task's status remains `in_progress` until Phase B's `rehydrate` action (or existing `reconcile_task_lifecycle`) reopens it.
* Dashboard-click dispatch paths unchanged.
* No regression: tasks with live sessions are not released.

**Estimated size:** ~30–60 LOC + 2 unit tests.

**Follow-on consideration (not in A.2 scope):** if heartbeat cadence proves too sparse for stale-detection latency, a separate `stale_sweep` cron job registered via `_register_system_cron_job` (see Phase C.1 for the registration pattern) running every 60s could close that gap. Not adding this in v1 — heartbeat cadence (~60s) is already in the right range.

---

## Phase B — Unstick Simone (the autonomy prerequisite)

**Goal:** Add the verbs that let Simone (and operators) take a `needs_review` task back into the autonomous loop. Without this, every later phase recovers faster but still escalates to humans.

### B.1 — Add `rehydrate`, `re_evaluate`, `redirect_to`, `request_revision` actions

**What:** Four new entries in `VALID_ACTIONS` (`task_hub.py:33`):

| Action | Caller | Effect |
|---|---|---|
| `rehydrate` | Operator (dashboard) — 🎯 default; Simone-callable via tool added in Phase D | Status `needs_review|blocked` → `open`; reset `metadata.dispatch.heartbeat_retry_count`, `todo_retry_count`, `last_disposition_reason`; preserve task body and history |
| `re_evaluate` | Simone (via tool) | Atomically: status → `in_progress`, agent_id → simone, attach failure context (last error, retry count, side-effect evidence, prior assignments summary) to the task description as a system-prompt addendum so Simone judges from evidence, not from title |
| `redirect_to(agent_id)` | Simone (via tool) | Atomically: status → `open`, set `metadata.dispatch.preferred_vp = agent_id`, optionally `request_revision` note attached as a comment for the next claimant |
| `request_revision(feedback)` | Simone (via tool) | For tasks Atlas/Cody completed-but-needs-rework: status → `open`, append feedback as a task comment with `author='simone-review'`, set `metadata.dispatch.revision_round += 1`, increment `max_retries` budget by 1 to absorb the revision |

**Code anchors:**
* `src/universal_agent/task_hub.py:33` — `VALID_ACTIONS` (add four)
* `src/universal_agent/task_hub.py:3954-` — `perform_task_action` (add four handlers)
* `src/universal_agent/task_hub.py:1395-1418` — eligibility filters (verify rehydrate output is eligible)
* New Simone tool surface: probably in `src/universal_agent/tools/` — register `task_re_evaluate`, `task_redirect_to`, `task_request_revision` tools

**Acceptance:**
* Operator clicks "rehydrate" on a `needs_review` task in the dashboard; task moves to `open` with counters reset; next dispatch sweep picks it up.
* Simone calls `task_re_evaluate(task_id)`; task moves to `in_progress` with failure context attached; she can see what failed in her prompt.
* Simone calls `task_redirect_to(task_id, agent_id="vp.general.primary")`; task moves to `open` with `preferred_vp=Atlas`; Phase C lane will pull it.
* Simone calls `task_request_revision(task_id, feedback="...")`; task reopens with comment appended; revision_round increments.

**Estimated size:** ~120-180 LOC + tool registrations + 6 unit tests.

### B.2 — Surface failure context in dashboard for operator review

**What:** When a task is in `needs_review`, the dashboard drawer should show: last error, retry count, last_disposition_reason, list of prior assignments (start time, agent_id, end time, result_summary). This is operator-facing rehydrate context so they know whether to rehydrate, redirect, or escalate.

**Code anchors:**
* `web-ui/` (Next.js) — task drawer component (find existing rendering)
* `src/universal_agent/gateway_server.py` — task-detail API endpoint (return failure context)

**Acceptance:**
* Click into a `needs_review` task; drawer shows failure context block with the four fields above.
* Rehydrate button visible only when status is `needs_review` or `blocked`; click triggers Phase B.1 action.

**Estimated size:** ~80-120 LOC (UI + API).

---

## Phase C — Atlas-direct-dispatch + Simone awareness

**Goal:** Stop throttling Atlas behind Simone's heartbeat. Add a lightweight independent dispatcher that auto-calls `vp_dispatch_mission` for Atlas-pre-tagged tasks. Preserve Simone's situational awareness via delegation events.

> ⚠️ **v2 correction:** Original v1 design described Atlas "claiming" tasks from `task_hub_items` directly. That's not how the VP path works. The actual flow is:
>
> 1. A `task_hub_items` row has `metadata.dispatch.preferred_vp = "vp.general.primary"` (set at creation by e.g. `proactive_convergence`).
> 2. Today, Simone reads that tag and calls `vp_dispatch_mission(vp_id="vp.general.primary", ...)` from her heartbeat.
> 3. That call writes a row into the SEPARATE `vp_missions` table (via `dispatch_mission_with_retry` in `tools/vp_orchestration.py:252-272`) AND creates a mirror row in `task_hub_items` with `source_kind="vp_mission"`, `agent_ready=False`, `status=delegated` (`tools/vp_orchestration.py:285-312`).
> 4. The external VP runtime (Atlas) polls `vp_missions` and picks up new work — it does NOT claim from `task_hub_items`.
>
> So the architectural throttle is at step 2: only Simone calls `vp_dispatch_mission`. The fix is to add an autonomous caller that does the same call without waiting for Simone's heartbeat.
>
> The existing pattern at `heartbeat_service.py:2225-2253` (signal curator) already does this kind of bypass for one specific use case — it calls `dispatch_vp_mission(vp_id="vp.general.primary", ...)` directly when pending signal cards exceed a threshold. Phase C generalizes that pattern.

### C.1 — Atlas-direct-dispatch cron-registered sweep

> ⚠️ **v3 correction:** v2 wrote the JSON path as `metadata.dispatch.preferred_vp` everywhere in this phase. **The actual storage path set by `services/proactive_convergence.py:562, 646` is `metadata.preferred_vp` (top-level), NOT `metadata.dispatch.preferred_vp`.** `queue_proactive_task` (`services/proactive_task_builder.py:135`) passes the metadata dict straight through to `upsert_item` without wrapping in a `dispatch` namespace. Fixed below. Atlas-direct *tracking* fields (the ones we ADD in this phase) still go in `metadata.dispatch.atlas_direct_*` for namespace consistency with retry counters; only the FILTER reads `metadata.preferred_vp`.

**What:** New script `src/universal_agent/scripts/atlas_direct_dispatch.py` registered via `_register_system_cron_job` to run every 60 seconds. Behavior:

```
1. Connect to runtime DB
2. Reuse _vp_active_counts (from todo_dispatch_service.py:155-179) to check current Atlas usage
3. If active_general >= UA_MAX_CONCURRENT_VP_GENERAL: log+exit (skip this tick)
4. Compute remaining_slots = UA_MAX_CONCURRENT_VP_GENERAL - active_general
5. Query task_hub_items where:
     - status = TASK_STATUS_OPEN
     - source_kind != "vp_mission"  (defensive: don't claim mirror rows — see 2026-05-07 Followup #3)
     - agent_ready = TRUE
     - json_extract(metadata_json, '$.preferred_vp') = 'vp.general.primary'   ← v3: top-level path
     - json_extract(metadata_json, '$.dispatch.atlas_direct_dispatched_at') IS NULL   (tracking, not source)
     - LIMIT remaining_slots
6. For each candidate, atomically:
     a. UPDATE task_hub_items SET metadata.dispatch.atlas_direct_dispatched_at = now()  (claim/lock)
     b. await dispatch_vp_mission(vp_id="vp.general.primary", objective=task.description,
                                    mission_type="proactive_general",
                                    idempotency_key=f"atlas-direct-{task_id}",
                                    source_session_id="atlas_direct_dispatch")
     c. Set metadata.dispatch.atlas_direct_lane / atlas_direct_assignee / atlas_direct_objective_preview (C.2)
7. Log dispatched count + slot usage for ops visibility
```

**Why cron-registered (not heartbeat-embedded):** keeps it independent of Simone's heartbeat lifecycle. If Simone's heartbeat is busy/stalled, the Atlas-direct dispatcher still runs every 60s.

**Defensive design notes:**
* The `source_kind != "vp_mission"` filter is critical — it prevents this dispatcher from accidentally claiming VP mirror rows, which are themselves marked `agent_ready=False` defensively (`tools/vp_orchestration.py:296-305` comment about Followup #3). Belt-and-suspenders.
* The `atlas_direct_dispatched_at` idempotency key prevents double-dispatch within a single sweep AND across restarts: if the gateway crashes between step 6a and 6b, the next sweep sees the timestamp and skips.
* The `dispatch_vp_mission` call carries its own `idempotency_key=f"atlas-direct-{task_id}"` — so even if step 6a's update somehow gets rolled back, the `dispatch_mission_with_retry` path's idempotency check in `vp_orchestration.py:252` catches the duplicate.

**Code anchors:**
* `src/universal_agent/services/agent_router.py:25-27` — reuse `AGENT_GENERAL` constant
* `src/universal_agent/services/todo_dispatch_service.py:155-179` — reuse `_vp_active_counts` (extract to shared module if not already)
* `src/universal_agent/tools/vp_orchestration.py:339-376` — call `dispatch_vp_mission` (the wrapper, not the `_impl` directly)
* `src/universal_agent/heartbeat_service.py:2225-2253` — existing precedent for "bypass Simone, call dispatch_vp_mission directly"
* `src/universal_agent/gateway_server.py:18140-18211` — `_register_system_cron_job` helper (the registration pattern)
* `src/universal_agent/gateway_server.py:18214-18232` — `_ensure_nightly_wiki_cron_job` (template for adding a new cron registration)
* `src/universal_agent/services/proactive_convergence.py:556, 562, 640, 646` — existing producers that set `preferred_vp = "vp.general.primary"`

**Registration:** add a new `_ensure_atlas_direct_dispatch_cron_job()` function in `gateway_server.py` mirroring `_ensure_nightly_wiki_cron_job` (`gateway_server.py:18214-18232`). Use:
```python
return _register_system_cron_job(
    system_job="atlas_direct_dispatch",
    default_cron="*/1 * * * *",
    default_timezone="UTC",
    command="!script universal_agent.scripts.atlas_direct_dispatch",
    description="Independent Atlas dispatcher for preferred_vp-tagged tasks; bypasses Simone-heartbeat throttle.",
    timeout_seconds=60,
    enabled=_proactive_cron_enabled("UA_ATLAS_DIRECT_DISPATCH_ENABLED"),
    cron_env_var="UA_ATLAS_DIRECT_DISPATCH_CRON",
    timezone_env_var="UA_ATLAS_DIRECT_DISPATCH_TIMEZONE",
)
```
🎯 default — start with `UA_ATLAS_DIRECT_DISPATCH_ENABLED=0` (off). Operator flips it on after dry-run testing.

**Acceptance:**
* Task with `metadata.preferred_vp = "vp.general.primary"` (v3-corrected path), `agent_ready=True`, `source_kind != "vp_mission"`, Atlas at 0/2 slots: gets dispatched by the cron within 60s; `vp_missions` row created; mirror row appears in `task_hub_items` with `source_kind="vp_mission"` and `agent_ready=False`.
* Same conditions but Atlas at 2/2 slots: skipped this tick, retried next tick.
* Untagged task (no `preferred_vp` metadata): NOT picked up by Atlas-direct.
* Task already dispatched (`metadata.dispatch.atlas_direct_dispatched_at` set): NOT re-dispatched.
* Simone's heartbeat unaffected — she continues to claim and route through `claim_next_dispatch_tasks` as before. Tasks dispatched by Atlas-direct never appear in her claim queue because they immediately get `status=delegated` once `vp_dispatch_mission` writes the mirror row.

**Estimated size:** ~180-240 LOC (new script + cron registration + shared `_vp_active_counts` extract + tests). +4 unit tests.

### C.2 — Simone awareness via task-metadata + briefing surface

> ⚠️ **v2 correction:** Original v1 design proposed inserting `delegation_fyi` rows into a `task_events` table. There is no `task_events` table in UA's schema (that's a Hermes pattern). UA has `task_hub_notifications` (dedup-keyed, no payload column — `task_hub.py:311-319`) and `task_hub_comments` (heavier; intended for human-readable thread content). Neither is the right home for structured FYI metadata. The cleaner pattern is to put the FYI data ON THE TASK ITSELF in `metadata.dispatch`, which is queryable and already feeds Simone's briefing context.

**What:**

1. The C.1 sweep already writes `metadata.dispatch.atlas_direct_dispatched_at = now()` and `metadata.dispatch.atlas_direct_lane = "atlas_direct"` on each task it dispatches (these are the idempotency markers — see C.1 step 6a).
2. Add the assignment-agent FYI: when the sweep calls `dispatch_vp_mission`, also write `metadata.dispatch.atlas_direct_assignee = "vp.general.primary"` and `metadata.dispatch.atlas_direct_objective_preview = description[:120]` for briefing readability.
3. Simone's heartbeat briefing context assembler already aggregates Task Hub state. Extend it with a "directly-dispatched-to-VPs in last 15min" section that queries:
   ```sql
   SELECT task_id, metadata FROM task_hub_items
   WHERE status IN ('delegated', 'pending_review')
     AND json_extract(metadata_json, '$.dispatch.atlas_direct_lane') IS NOT NULL
     AND json_extract(metadata_json, '$.dispatch.atlas_direct_dispatched_at') > datetime('now', '-15 minutes')
   ```
   (v3 note: the `dispatch.atlas_direct_*` paths in this query are the TRACKING fields ADDED by C.1, which we correctly namespace under `dispatch.` for consistency with retry counters. The source `preferred_vp` field that triggers the dispatch is at `metadata.preferred_vp` — top-level — per the v3 correction at C.1.)
   Render as: `Atlas (direct-dispatch): N missions in flight — [task_id_1: "objective preview..."], [task_id_2: ...]`

**Why on the task, not in a side table:** The data IS task-scoped (which task, by which lane, when). Putting it in metadata.dispatch means: (a) it's atomic with the dispatch itself (no two-table consistency window); (b) Simone's existing task-fetch logic surfaces it without a JOIN; (c) the dashboard task drawer already renders metadata.dispatch; (d) no new table or migration.

**Code anchors:**
* `src/universal_agent/task_hub.py` — existing `metadata_json` field on `task_hub_items`; no schema change
* `src/universal_agent/heartbeat_service.py:483-495` — area where the "Atlas slots in use" briefing line lives (`f"  Atlas (vp.general.primary): {active_general}/{max_general} slots in use"` at line 488); the new "direct-dispatched in last 15min" section goes alongside this
* Phase B.1's `redirect_to` / `request_revision` verbs can act on these tasks since they're queryable by `task_id`

**Acceptance:**
* Atlas-direct dispatched task has `metadata.dispatch.atlas_direct_dispatched_at`, `atlas_direct_lane`, `atlas_direct_assignee`, `atlas_direct_objective_preview` fields populated.
* Simone's next heartbeat briefing includes a section listing direct-dispatched missions from the last 15min with task_id + preview.
* Simone's prompt-context includes the active direct-dispatched task list — so if she wants to intervene via Phase B.1's `redirect_to(task_id, agent_id="simone")` to take it back, she has the ID.
* Dashboard task drawer for a direct-dispatched task shows the lane info ("dispatched by atlas_direct at T").

**Estimated size:** ~80-120 LOC + 2 unit tests (heartbeat briefing assembly + dashboard drawer render).

---

## Phase D — Attempt history (`task_hub_runs`)

**Goal:** Give Simone the rich failure context she needs to make Phase B's `re_evaluate` verb actually useful, and unlock dashboard "what happened on each attempt" views.

> 📌 **v3 note (Kevin's memory check):** Yes — this phase IS the Hermes `task_runs` table duplication we identified in the original investigation as Recommendation #2 in §5 of the comparison report. Hermes' table at `kanban_db.py:832-852` carries one row per attempt with claim/PID/heartbeat/outcome/summary/metadata/error. UA's `task_hub_assignments` is the closest existing analogue but is claim-ledger-only (no closing summary/error/outcome). Phase D adds the equivalent of Hermes' attempt-history table, scoped to UA's needs (no PID/heartbeat fields — those are Phase F observability that flows into this table when applicable). Phase D + the Phase B verbs that consume it are the two halves of "Simone judges from rich failure evidence" (the LLM-Native Intelligence Design principle from CLAUDE.md applied to recovery decisions).

### D.1 — `task_hub_runs` table + close-on-finalize wiring

**What:** New table parallel to `task_hub_assignments` but holding per-attempt outcome/summary/metadata/error. Schema:

```sql
CREATE TABLE task_hub_runs (
    run_id              TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL,
    assignment_id       TEXT,                         -- FK to task_hub_assignments
    agent_id            TEXT,
    started_at          TIMESTAMP NOT NULL,
    ended_at            TIMESTAMP,
    outcome             TEXT,
    -- outcome: completed | failed | reclaimed | timed_out | crashed | gave_up
    summary             TEXT,                         -- closing handoff summary
    metadata_json       TEXT,                         -- structured handoff facts
    error               TEXT                          -- closing error (if failed)
);

CREATE INDEX idx_runs_task ON task_hub_runs(task_id, started_at);
CREATE INDEX idx_runs_outcome ON task_hub_runs(outcome);
```

Add `_open_run(...)` and `_close_run(...)` helpers. Call `_open_run` from `claim_next_dispatch_tasks` (and from Atlas-direct claim in Phase C); call `_close_run` from every termination path in `finalize_assignments` and `reconcile_task_lifecycle`.

🎯 default — additive only: existing code paths continue to work; runs table is enriched but its absence doesn't break anything during rollout.

**Code anchors:**
* `src/universal_agent/task_hub.py:199-332` — schema (add table + indexes)
* `src/universal_agent/task_hub.py:1628-1710` — claim path (add `_open_run`)
* `src/universal_agent/task_hub.py:2977-3169` — finalize path (add `_close_run` for each branch)
* `src/universal_agent/task_hub.py:2621-2849` — reconcile path (add `_close_run` for orphan-recovery)

**Acceptance:**
* Each claim creates a `task_hub_runs` row with `outcome=NULL`, `started_at=now`.
* Successful completion closes with `outcome='completed'` + `summary`.
* Heartbeat retry / timeout / crash closes with appropriate outcome + error.
* Querying `SELECT * FROM task_hub_runs WHERE task_id = ? ORDER BY started_at` returns full attempt history.

**Estimated size:** ~150-250 LOC + 1 migration + 6 unit tests.

### D.2 — Phase B verbs consume `task_hub_runs`

**What:** Update `re_evaluate` action to surface the last N runs (default 3, configurable) as failure context in Simone's prompt addendum. Update operator dashboard `needs_review` drawer to render attempt history.

**Code anchors:**
* Phase B.1's `re_evaluate` handler (enrich the prompt addendum)
* Phase B.2's drawer (render run history table)

**Acceptance:**
* Simone calling `task_re_evaluate(task_id)` on a task with 3 prior failed runs sees all 3 errors / summaries / outcomes in her prompt.
* Operator viewing a `needs_review` task sees a chronological list of attempts with outcome / error per row.

**Estimated size:** ~80-120 LOC + minor UI work.

---

## Phase E — Cody Anthropic-vs-ZAI toggle

**Goal:** Let Cody run on the Anthropic Max plan for harder coding tasks, with full SDK agent-team / parallel-tool capabilities preserved. Default stays ZAI for cost.

> ⚠️ **v3 correction (replaces v2 entirely):**
>
> v2 said: "VP-Cody runs in an external runtime outside this codebase" → "Phase E is HALF the work; the other half is in the VP runtime" → "E.3 is coordination work tracked separately."
>
> **All of that is wrong.** The VP runtime IS in this repo at `src/universal_agent/vp/worker_loop.py` (937 LOC). The `_select_client_for_mission` method at `worker_loop.py:714-741` chooses one of three execution clients based on `mission.payload.execution_mode`:
>
> | execution_mode | Client | Code path | Cody PID owned by UA? |
> |---|---|---|---|
> | `"sdk"` (default) | `ClaudeCodeClient` (Cody) / `ClaudeGeneralistClient` (Atlas) | `vp/clients/claude_code_client.py:25-` — uses in-process `ProcessTurnAdapter` from `EngineConfig` | No (in-process, no separate PID) |
> | `"cli"` | `ClaudeCodeCLIClient` | `vp/clients/claude_cli_client.py:191` — `asyncio.create_subprocess_exec("claude", ...)` | **Yes** — UA spawns the `claude` subprocess |
> | `"dag"` | `DagClient` | `vp/clients/dag_client.py` — deterministic flow runner | No (in-process, not LLM) |
>
> Plus Kevin's correctly-recalled nuance: Cody operates across MULTIPLE distinct WORKSPACE environments, all in this repo:
>
> | Cody environment | Code path | Subprocess? | Workspace |
> |---|---|---|---|
> | VP SDK in-process | `claude_code_client.py:42` `_resolve_workspace_dir(...)` | No | Any external path from constraints |
> | VP CLI subprocess | `claude_cli_client.py:191` | Yes | Any external path via `cwd` |
> | Autonomous-mission worktree | `vp/autonomous_mission_executor.py:290` `execute_autonomous_mission(...)` | (mode-dependent) | Fresh git worktree off `feature/latest2` |
> | Demo workspace | `services/cody_implementation.py:312-365` `run_in_workspace(...)` | Yes (synchronous `subprocess.run`) | `/opt/ua_demos/<id>/` |
>
> The toggle is fully implementable in this repo, applies to ALL of those modes, and requires NO external coordination. v2's E.3 is removed below; the work is reorganized into per-mode toggle plumbing.

### E.1 — Toggle resolver + per-task / per-system column (this PR)

**What:**

1. New env var `UA_CODY_DEFAULT_MODE` ∈ `{"zai", "anthropic"}`, default `"zai"`. System-wide default.
2. New nullable column `cody_mode TEXT` on `task_hub_items`. Per-task override; if set, takes precedence over env var.
3. New helper `services/cody_mode.py` exporting `resolve_cody_mode(task: dict) -> Literal["zai", "anthropic"]`. Reads task's `cody_mode` first, then env, then default.
4. Plumb the resolved value into `vp_dispatch_mission`'s metadata (`tools/vp_orchestration.py:266-271`) so it lands in the `vp_missions.payload_json` for `worker_loop` to read on dispatch.

**Code anchors:**
* `src/universal_agent/tools/vp_orchestration.py:209-272` — `_vp_dispatch_mission_impl` (add cody_mode plumbing here)
* `src/universal_agent/task_hub.py:199-332` — schema (add `cody_mode` column to `task_hub_items`)

**Acceptance:**
* Task with `cody_mode=NULL` AND `UA_CODY_DEFAULT_MODE="zai"`: resolver returns `"zai"`; metadata reaches `vp_missions.payload_json["cody_mode"] = "zai"`.
* Task with `cody_mode="anthropic"`: resolver returns `"anthropic"` regardless of env; metadata reaches `vp_missions.payload_json["cody_mode"] = "anthropic"`.

**Estimated size:** ~60-90 LOC + migration + 3 unit tests.

### E.2 — Apply the toggle in each Cody execution mode

**What:** Each of the four Cody execution paths reads `payload.cody_mode` (or task metadata for the demo workspace path) and applies the appropriate runtime configuration.

**E.2.a — VP CLI subprocess** (`vp/clients/claude_cli_client.py:391-407`):
* Today `_build_cli_env` does `env = dict(os.environ)` — inherits parent's ANTHROPIC_* (currently ZAI routing because UA daemon runs on ZAI per CLAUDE.md).
* When `cody_mode == "anthropic"`: scrub ANTHROPIC_* from the env (mirroring `cody_implementation.py:269-280`'s `_scrubbed_env()`). The `claude` subprocess will then use the project-local OAuth (Anthropic Max) at the workspace.
* When `cody_mode == "zai"`: keep current behavior (inherit ZAI routing from parent env).
* **Critical:** when in Anthropic mode, ALSO set `env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"` so the toggle gets the agent-team capability that's the whole point of Anthropic mode (already conditional on `enable_agent_teams` arg at line 395; we'd want to default that to True for Anthropic mode).

**E.2.b — VP SDK in-process** (`vp/clients/claude_code_client.py:25-` + `EngineConfig`):
* In-process Anthropic SDK call. The "model" is determined by whatever `EngineConfig` / `ProcessTurnAdapter` resolves at runtime, which today reads env vars at the UA process level (ZAI today).
* When `cody_mode == "anthropic"`: pass an explicit override into `EngineConfig` so the in-process adapter routes to Anthropic (Opus/Sonnet 4.x) instead of GLM. Mechanism: add a kwarg to `EngineConfig` like `model_routing_override="anthropic"` and have the adapter consume it.
* When `cody_mode == "zai"`: existing behavior (resolves from process-level env).
* This is the trickier of the two — it requires a small extension to `EngineConfig` and the adapter's model-resolution path. Need to verify the adapter has a clean place to inject this (the `EngineConfig` already accepts custom kwargs at `vp/clients/claude_code_client.py:52`).

**E.2.c — Autonomous-mission worktree** (`vp/autonomous_mission_executor.py:290`):
* `execute_autonomous_mission` is the tier-2 entry point for repo-mutating missions. It provisions a worktree, calls a `patch_fn`, runs syntax checks + tests, opens a PR.
* The `patch_fn` itself is caller-supplied; if the patch_fn invokes `claude` (CLI mode), E.2.a's env-scrubbing applies. If it invokes a SDK adapter, E.2.b applies.
* No direct change in this file — the toggle propagates through whichever client the patch_fn ends up calling.

**E.2.d — Demo workspace** (`services/cody_implementation.py:312-365`):
* `run_in_workspace` already uses `_scrubbed_env()` (line 269-280) which removes `ANTHROPIC_*` — so demo workspaces ALREADY use Anthropic Max by design.
* The toggle is moot here. Document this as "demo workspace is always Anthropic mode regardless of the per-task toggle" so an operator who sets `cody_mode="zai"` on a `cody_demo_task` doesn't get confused.

**Code anchors:**
* `vp/clients/claude_cli_client.py:391-407` — `_build_cli_env` (E.2.a edit)
* `vp/clients/claude_code_client.py:52` — `EngineConfig(...)` instantiation (E.2.b edit)
* `vp/autonomous_mission_executor.py:290` — `execute_autonomous_mission` (E.2.c — no change, just documentation)
* `services/cody_implementation.py:269-280` — `_scrubbed_env` (E.2.d — reference only, no change)

**Acceptance:**
* CLI mode + `cody_mode=anthropic`: the spawned `claude` subprocess's env has NO `ANTHROPIC_*` vars (verified by integration test that captures `env` passed to `create_subprocess_exec`).
* CLI mode + `cody_mode=zai`: env unchanged from today's behavior.
* SDK mode + `cody_mode=anthropic`: `EngineConfig` constructed with `model_routing_override="anthropic"`; adapter resolves to Anthropic model (verified with a unit test mocking the adapter).
* SDK mode + `cody_mode=zai`: existing behavior.
* Demo workspace: continues to scrub ANTHROPIC_* unconditionally (verified by existing tests; no regression).

**Estimated size:** ~80-140 LOC across the three edit sites + 4 unit tests + 1 integration test (CLI env-scrub round-trip).

### E.3 — Dashboard surface for Anthropic-mode awareness

**What:** When a Cody session is running in Anthropic mode (per the `vp_missions` row's `payload.cody_mode`), the dashboard active-agents tile shows a tag (e.g., "Cody [Anthropic]") so an operator looking at "active_coder=1" understands this session has internal fan-out and might be doing more work than the count suggests.

**Code anchors:**
* `src/universal_agent/services/mission_control_tiles.py` — active-agents tile (read `vp_missions.payload.cody_mode`)
* `web-ui/` — tile rendering

**Acceptance:**
* Operator sees "Cody [Anthropic]" in the active-agents tile when an active Cody mission has `payload.cody_mode = "anthropic"`.
* Briefing context for Simone includes the same tag (so she also knows when Cody is in Anthropic mode and can adjust her delegation calculus accordingly).

**Estimated size:** ~40-60 LOC (mostly UI + a small tile-data extension).

### E.2 — Dashboard surface for Anthropic-mode awareness

**What:** When a Cody session is running in Anthropic mode, the dashboard active-agents tile shows a tag (e.g., "Cody [Anthropic]") so an operator looking at "active_coder=1" understands this session has internal fan-out and might be doing more work than the count suggests.

**Code anchors:**
* `src/universal_agent/services/mission_control_tiles.py` — active-agents tile
* `web-ui/` — tile rendering

**Acceptance:**
* Operator sees "Cody [Anthropic]" in the active-agents tile when a Cody session is in Anthropic mode.
* Briefing context for Simone includes the same tag so she knows.

**Estimated size:** ~40-60 LOC (mostly UI).

---

## Phase F — Owned-subprocess observability + Task Hub integration

**Goal:** Add Hermes-style subprocess-level observability (PID tracking, exit classification, protocol-violation detection) to the worker subprocesses UA actually owns. Per-task wall-clock timeout flows in via Task Hub so it's per-task-configurable instead of per-spawn-site-configurable.

> ⚠️ **v3 correction (replaces v2):**
>
> v2 said the only UA-owned subprocess class was cron `!script` jobs, which already have timeout+kill. That was WRONG — VP CLI client (`vp/clients/claude_cli_client.py:191`) is also UA-owned. Reverified picture:
>
> | Worker class | UA owns PID? | Already has timeout? | Already has kill? |
> |---|---|---|---|
> | **Cron `!script` jobs** | Yes (`cron_service.py:1129`) | Yes (`asyncio.wait_for` at line 1137) | Yes (`proc.kill()` at line 1140) |
> | **VP CLI client** | **Yes** (`claude_cli_client.py:191`) | **Yes** (deadline loop at line 254-266) | **Yes** (`_kill_process` at line 529 — SIGTERM then SIGKILL) |
> | **Demo workspace Cody** | Yes (`cody_implementation.py:312-365`) | Yes (synchronous `subprocess.run(timeout=timeout)`) | N/A — synchronous, raises `TimeoutExpired` on its own |
> | **VP SDK clients** | No (in-process Anthropic SDK) | N/A | N/A |
> | **ToDo dispatch** | No (in-process `GatewaySession`) | N/A | N/A |
> | **Simone heartbeat** | No (long-lived daemon) | N/A | N/A |
>
> So the genuinely owned subprocess set has THREE classes (cron, VP CLI, demo workspace), all of which ALREADY have timeout+kill at the spawn site. **What Phase F adds is unified observability into Task Hub, NOT new timeout/kill machinery.** Specifically: record `worker_pid` on `task_hub_assignments`, classify exit (clean/nonzero/signaled/timeout), feed Phase D's `task_hub_runs`, and detect protocol violations (clean exit but task not closed).

### F.1 — `worker_pid` tracking + exit classification across all owned-subprocess sites

> ⚠️ **v3 expansion:** v2 limited this to cron only. v3 includes VP CLI client and demo workspace too, since both are UA-owned subprocesses.

**What:**

1. Add `worker_pid INTEGER` to `task_hub_assignments`. NULL for in-process assignments (SDK mode, ToDo, heartbeat); populated for cron / VP CLI / demo subprocess assignments.
2. Three spawn sites record `proc.pid` on the corresponding `task_hub_assignments` row at subprocess start:
   * `cron_service.py:1135` — record after `await asyncio.create_subprocess_exec`
   * `claude_cli_client.py:191` — record after `await asyncio.create_subprocess_exec`
   * `cody_implementation.py:336-345` — record before `subprocess.run` (synchronous; pid available via the Popen-style flow if we restructure, OR record `os.getpid()` from inside the subprocess if cleaner — needs design choice during implementation)
3. Three completion sites record `returncode` + classified outcome:
   * `clean_exit_zero` — rc=0 AND task closed properly (success path)
   * `clean_exit_zero_no_disposition` — rc=0 but task still in_progress (protocol violation, see F.3)
   * `nonzero_exit` — rc != 0 (real error)
   * `signaled` — process killed by signal (OOM, SIGKILL, etc.)
   * `timeout_killed` — UA's own timeout machinery killed it
4. Outcomes feed Phase D's `task_hub_runs` close path uniformly.

**Code anchors:**
* `src/universal_agent/cron_service.py:1129-1158` — cron spawn + completion (add PID + classification)
* `src/universal_agent/vp/clients/claude_cli_client.py:188-234` — CLI spawn + completion (add PID + classification; `_monitor_cli_output` at line 237 is where rc is observed)
* `src/universal_agent/services/cody_implementation.py:312-365` — demo workspace spawn (smaller refactor needed if we want to capture pid pre-completion; alternative: just record returncode + classification post-hoc)
* `src/universal_agent/task_hub.py:199-332` — schema (add `worker_pid INTEGER`)
* Hermes pattern reference: `kanban_db.py:2879-2911` (`_classify_worker_exit`) for the outcome classification taxonomy

**Acceptance:**
* Cron `!script` run: `worker_pid` recorded; outcome classified.
* VP CLI mission: `worker_pid` recorded; outcome classified (including `timeout_killed` when `_kill_process` fires).
* Demo workspace run: `returncode` + classification recorded; PID recorded if implementation permits.
* Non-subprocess assignments: `worker_pid` stays NULL (no regression).

**Estimated size:** ~120-180 LOC across the three spawn sites + 1 schema migration + 5 unit tests.

### F.2 — Per-task `max_runtime_seconds` flowing into all owned-subprocess timeouts

> ⚠️ **v3 expansion:** v2 limited this to cron only. v3 covers cron + VP CLI client + demo workspace, since each has its own existing timeout config we can override with the per-task value.

**What:** New `max_runtime_seconds INTEGER` column on `task_hub_items` (per task) AND `UA_TASK_DEFAULT_MAX_RUNTIME_SECONDS` env var (system default, 🎯 default 7200s / 2 hours). Three spawn sites consume it:

```
effective_timeout = task.max_runtime_seconds
                    OR env_default
                    OR site-specific existing timeout (cron_job.timeout_seconds, CLI default, demo default)
```

* **Cron** (`cron_service.py:1137`): replace `timeout=timeout_seconds` with `timeout=effective_timeout`.
* **VP CLI** (`claude_cli_client.py:254`): the deadline loop already takes a `timeout_seconds` parameter; pass `effective_timeout` from the mission's task metadata.
* **Demo workspace** (`cody_implementation.py:316, 342`): `subprocess.run(..., timeout=effective_timeout)` — already takes a timeout kwarg; just plumb the value through.

No new kill machinery — each site already has SIGTERM/SIGKILL or sync TimeoutExpired. F.2 is per-task configurability over what the spawn sites already do.

For in-process assignments (SDK, ToDo, heartbeat), F.2 does NOT kill anything. The `max_runtime_seconds` field becomes purely a stale-detection trigger: if `elapsed > max_runtime_seconds` AND the assignment is still seized, Phase A.2's `release_stale_assignments` catches it on the next sweep and marks the assignment failed. Task reopens via existing reconcile path.

**Code anchors:**
* `src/universal_agent/task_hub.py:199-332` — schema (add `max_runtime_seconds INTEGER`)
* `src/universal_agent/cron_service.py:1137` — cron `asyncio.wait_for(..., timeout=timeout_seconds)`
* `src/universal_agent/vp/clients/claude_cli_client.py:228-232` — CLI `timeout_seconds` parameter to `_monitor_cli_output`
* `src/universal_agent/services/cody_implementation.py:316, 342` — demo `subprocess.run(timeout=timeout)`
* `src/universal_agent/task_hub.py:3259-3308` — `release_stale_assignments` (read max_runtime_seconds for in-process stale cutoff)

**Acceptance:**
* Cron-launched task with `task.max_runtime_seconds = 300`: cron uses 300s for `asyncio.wait_for` timeout regardless of cron job's `timeout_seconds`.
* VP CLI mission with `task.max_runtime_seconds = 1800`: CLI client kills the `claude` subprocess at 1800s elapsed.
* Demo workspace task with `task.max_runtime_seconds = 600`: `subprocess.run` raises TimeoutExpired at 600s.
* In-process assignment with `max_runtime_seconds = 3600` running >3600s: gets stale-released on next dispatch_sweep (Phase A.2 wiring).
* `max_runtime_seconds = NULL`: falls back to env default → site-specific existing timeout. No regression.

**Estimated size:** ~80-130 LOC across the three plumbing sites + migration + 3 unit tests.

### F.3 — Protocol-violation detection across owned-subprocess sites

> ⚠️ **v3 expansion:** v2 limited this to cron. v3 covers all three owned-subprocess sites since each can exhibit the clean-exit-no-disposition failure mode.

**What:** When an owned subprocess exits with `rc=0` but the linked task is still `in_progress` (no completion mutation was recorded via `finalize_assignments` or `perform_task_action(action="complete")`), classify as a protocol violation — park the task in `needs_review` with reason `protocol_violation_<site>_clean_exit_no_disposition`. Phase B.1's `rehydrate` / `re_evaluate` verbs then act on it.

Three site-specific reason strings:
* `protocol_violation_cron_clean_exit_no_disposition` (cron `!script`)
* `protocol_violation_vp_cli_clean_exit_no_disposition` (VP CLI client)
* `protocol_violation_demo_clean_exit_no_disposition` (demo workspace)

🎯 default — failure_limit=1 (immediate park, don't retry). A subprocess that returns 0 without closing its task is going to do exactly the same thing on retry. Hermes precedent: `kanban_db.py:3256-3271`.

**Out of scope for F.3:** in-process workers (SDK clients, ToDo dispatch, heartbeat). Those don't have an exit code to observe; their protocol violations show up as "completed run without explicit disposition" which is already handled by `finalize_assignments` at `task_hub.py:2978-2996`.

**Code anchors:**
* F.1's PID + outcome recording at each site — feeds the classification
* `src/universal_agent/task_hub.py` — finalize-on-task-completion mutation (`finalize_assignments` and `perform_task_action(action="complete")` are the recorded paths to check against)
* Hermes pattern: `kanban_db.py:3256-3271, 3320-3331`

**Acceptance:**
* Cron `!script` exits rc=0 with linked task still `in_progress`: task → `needs_review` with reason `protocol_violation_cron_clean_exit_no_disposition`.
* VP CLI mission exits rc=0 with task still `in_progress`: task → `needs_review` with reason `protocol_violation_vp_cli_clean_exit_no_disposition`.
* Demo workspace returns rc=0 without writing manifest.json (or other completion marker): task → `needs_review` with reason `protocol_violation_demo_clean_exit_no_disposition`.
* Any site exiting rc!=0 OR signaled: increment failure counter via existing finalize_assignments path; retry per existing rules.

**Estimated size:** ~80-120 LOC across the three sites + 4 unit tests.

### F.4 — Out of scope

Items explicitly NOT in Phase F:
* In-process PID-liveness machinery — SDK clients, ToDo, heartbeat are in-process; they have no PID separate from the gateway daemon. Liveness is handled by session membership + Phase A.2 stale-assignment release.
* New SIGTERM/SIGKILL primitives — every owned-subprocess site already has them at the spawn site. F.2 reuses; F.1 + F.3 observe.
* New `enforce_max_runtime` loop in `dispatch_sweep` — duplicates per-site machinery. Stale-assignment release (A.2) handles the in-process drift case.

---

## Cross-phase verification & rollout

### Pre-Phase-A baseline measurement

Before any Phase ships, capture a 7-day baseline:

* Task completion rate per agent (Simone direct, Atlas, Cody)
* `needs_review` arrival rate per disposition reason
* Mean time stuck in `needs_review` before operator action
* Atlas slot utilization (0/2, 1/2, 2/2 distribution)
* Stale-assignment count per day

Re-measure after each Phase to confirm directional improvement.

### Phase ordering rationale

* **A first** because: small, additive, no behavior change, validates schema-migration discipline.
* **B before C** because: Atlas-direct (C) will surface tasks that need redirect/feedback; without B's verbs, Simone can't act on Atlas's output.
* **D after B** because: Phase B verbs work with whatever context exists today; D enriches them. Order allows B to ship and earn confidence first.
* **E independent** because: Cody Anthropic toggle doesn't depend on B/C/D. Can ship in parallel if engineering bandwidth allows.
* **F last** because: PID-aware reliability + protocol-violation detection are the most code-heavy; we want the rest of the autonomy story working first so F has a clear playground.

### Per-phase deploy gate

Each phase must:
1. Pass unit tests (`uv run pytest tests/unit/`)
2. Pass `ruff check`
3. Pass `py_compile` on every changed `.py` (the existing `pr-validate.yml` gate)
4. Have its acceptance criteria verified on the dev box AND post-deploy on the VPS via `/api/v1/version` SHA confirm + the production check from CLAUDE.md Production Verification Rules.

### Phase F's ZAI-mode safety check

> ⚠️ **v2 correction:** Original v1 said Phase F's SIGTERM machinery needed safety testing for Cody-Anthropic mid-fanout. **F no longer adds SIGTERM machinery** — cron already has it (`proc.kill()` at `cron_service.py:1140`) and F.2 just makes the timeout per-task-configurable. So there's no new kill primitive to safety-check. The original concern (Anthropic-mode internal fan-out + parent SIGTERM = orphaned sub-processes) is real but lives in the VP runtime's launcher code (Phase E.3), not Phase F. Tracking note carried forward to E.3's coordination work.

---

## Future phases (not in scope here, captured for memory)

* **Capability-aware tiered judge** (§13.5): Once Phase E lands, Cody-on-Anthropic becomes available as a Tier 2 judge for tasks where Simone-on-ZAI's reasoning ceiling isn't enough. Adds `needs_orchestrator_review` state and routing to it. Future phase.
* **Atlas-as-judge for second opinions** (§13.7): Atlas could handle "second-opinion review of Simone-produced work" — a different shape than Simone-judges-Atlas. Future phase.
* **Raise `UA_MAX_CONCURRENT_VP_GENERAL` from 2** based on Phase C utilization metrics. Operational tuning, not a code change.
* **Hermes-style hallucination gate** for created cards: only relevant if we add a `kanban_create`-equivalent worker tool. Not in scope today.

---

## Estimated total effort

(v3 estimates reflect Phase E and F expansions: VP CLI is in-repo; toggle and reliability adaptations cover all three owned-subprocess sites.)

| Phase | LOC range | Test count | DB migration? |
|---|---|---|---|
| A | 80–140 | 5 | 1 |
| B | 200–300 | 8 | 0 |
| C | 260–360 | 6 | 0 |
| D | 230–370 | 8 | 1 |
| E | 180–290 (E.1 + E.2 spans 4 modes + E.3 dashboard) | 7 unit + 1 integration | 1 |
| F | 280–430 (3 spawn sites × 3 sub-phases) | 12 | 1 |
| **Total** | **1230–1890 (all in this repo)** | **46 unit + 1 integration** | **4 migrations** |

(v3 vs v2: +1 column for cody_mode in E folds in same migration as E.1; +1 PID + max_runtime columns merged into Phase F's single migration.)

Spread across 6 phases means each PR is reviewable. The A → F sequence assumes one phase ships per ~1-2 week cadence depending on bandwidth.

**v3 update on out-of-repo work:** None. v2's E.3 "external VP runtime coordination" was based on a wrong reading; the VP runtime is at `vp/worker_loop.py` IN this repo. All work is in-repo.

---

## Decision log (defaults to confirm or override)

These are decisions I made by default in this plan. Marking them so Kevin can correct any before implementation starts.

| # | Decision | Default | Rationale |
|---|---|---|---|
| 1 | Cody toggle surface | env var (system default) + per-task field (override) | Mirrors per-task `max_retries` pattern |
| 2 | Atlas direct-dispatch eligibility | Only `metadata.dispatch.preferred_vp == "vp.general.primary"` tagged tasks | Conservative; widen post-utilization-data |
| 3 | Atlas direct-dispatch failure | Reopen with preferred_vp cleared (next attempt = Simone-routed) | Simpler than tiered fallback |
| 4 | `rehydrate` caller | Operator-only via dashboard initially; Simone-callable in Phase D | Don't give Simone the verb until task_runs gives her context to judge from |
| 5 | `redirect_to` / `request_revision` callers | Simone only | Keep verb scope narrow; revisit if Atlas-as-judge ships |
| 6 | Awareness mechanism | Event row in existing `task_events` / `task_hub_notifications` + briefing surface | Reuses existing event infra |
| 7 | Atlas-direct sweep cadence | Every 60 seconds | Independent of Simone's heartbeat; tune post-deploy |
| 8 | `task_hub_runs` rollout | Additive only — existing code paths still work without it | Don't gate Phase B on Phase D |
| 9 | F.2 default `max_runtime_seconds` env value | `UA_TASK_DEFAULT_MAX_RUNTIME_SECONDS=7200` (2 hours) | Generous default; per-task override is the precision knob |
| 10 | F scope (v3): observability + per-task timeout across cron + VP CLI + demo subprocess sites | Each spawn site already has its own SIGTERM/SIGKILL; F adds PID tracking + exit classification + per-task timeout configurability + protocol-violation detection | v3 correction (vs v2 narrowing): VP CLI client is also UA-owned (`claude_cli_client.py:191`); demo workspace is too. Phase F applies across all three owned subprocess sites, all of which already have kill primitives. |
| 11 | Phase E (v3): toggle is fully in-repo, applies across 4 Cody execution paths | E.1 adds resolver + column. E.2 applies the toggle in CLI / SDK / autonomous-mission / demo-workspace modes. E.3 is dashboard surface. No external VP runtime coordination. | v3 correction (vs v2 split): VP runtime is at `vp/worker_loop.py` IN this repo. v2 wrongly framed it as external. |
| 12 | Phase C uses metadata.dispatch.atlas_direct_* tracking + reads metadata.preferred_vp source | Atlas-direct dispatcher reads top-level `metadata.preferred_vp` (set by `proactive_convergence`); writes its own tracking fields under `metadata.dispatch.atlas_direct_*` for namespace consistency with retry counters | v3 correction (vs v2 wrong path): `metadata.preferred_vp` is the actual storage location at `proactive_convergence.py:562, 646`. v2 wrongly wrote `metadata.dispatch.preferred_vp` everywhere. SQL queries corrected to `json_extract(..., '$.preferred_vp')`. |

---

## What this plan deliberately does NOT include

* No refactor of `route_all_to_simone`. Simone-first remains the default for general task routing; Atlas-direct is an additive lane, not a replacement.
* No new state machine. We work with the existing 10 task statuses.
* No changes to Simone's heartbeat tick interval or her existing dispatch_sweep behavior.
* No changes to VP runtime / external mission orchestration. We touch how UA invokes Cody; we don't touch what happens inside a Cody session.
* No changes to the cron service's existing timeout machinery in Phase F (we ADD per-task max_runtime; existing cron-level timeouts stay).
* No "fix Simone's prompt to delegate more" work. That's a prompt-engineering follow-up after Phase C ships and we measure utilization.
