# Hermes v0.13.0 Kanban + Tenacity vs. UA Task Hub — Comparison Report

**Date:** 2026-05-10
**Branch:** `claude/hermes-agent-investigation-GfvTQ`
**Author:** investigation by Claude Code on Kevin's prompt
**Status:** First-cut qualitative report. No code changes. The technical appendix is deliberately exhaustive so a follow-up implementation plan can be built directly from it.

> **Read this first if you only have 60 seconds:** Section 1 (TL;DR) → Section 4 (parity matrix) → Section 5 (the five adaptations worth considering). Sections 7–9 are the durable technical appendices.

---

## 1. TL;DR

1. **Hermes shipped a real Kanban+retry kernel; we have one too — but ours optimizes for "human-judged completion," theirs optimizes for "headless worker resilience."** Same word, different center of gravity. Theirs is built around *worker subprocesses on the same host* that the dispatcher can SIGTERM; ours is built around *heterogeneous principals (Simone / Cody / VP / cron)* that the dispatcher cannot kill, only re-route.
2. **Three Hermes ideas are clearly worth adopting in some form**: (a) per-task `max_retries` override on top of the global limit, (b) the "clean-exit-without-completion" protocol-violation gate, (c) an explicit `task_runs` attempt-history table separate from `tasks`. None of these refactor our flow engine; they slot in.
3. **Two Hermes ideas are worth thinking about but probably not adopting wholesale**: (d) per-task `max_runtime_seconds` with SIGTERM/SIGKILL, (e) PID-liveness reaper with zombie detection. Both are valuable in Hermes' single-host worker-subprocess model and largely unhelpful in ours, where the "worker" is often a remote agent or a long-lived heartbeat we wouldn't want to kill anyway.
4. **One Hermes idea we already have, just at a different layer**: exponential backoff. Our `heartbeat_service._heartbeat_retry_delay_seconds` (10s × 2^N capped) gates *the heartbeat scheduler* when a tick fails. Hermes has no per-task delay either — they just bound retries by count and trip the circuit. So neither system back-offs at the task-reopen layer; we both back-off at the dispatcher-cadence layer.
5. **The single biggest functional gap they have and we don't is per-task `max_runtime_seconds`** — a kill-the-worker-after-N-seconds primitive. We have `release_stale_assignments(stale_after_seconds=1800)` but it's manual-cron, not enforced per-task, and it doesn't actually kill anything (we have no PID to kill). For our system this is more of a "park the task and let the operator decide" knob than a "SIGTERM the worker" knob, and that distinction matters for any adaptation.

The headline answer to "is Hermes' approach so amazing that we're wrong?" — **no.** Their approach is excellent for a different problem class (headless local workers). Ours is correct for ours (heterogeneous principals + human-in-the-loop briefings). The adaptable wins are bounded and additive.

---

## 2. Why this comparison matters

Kevin's framing was that NousResearch's Hermes Agent v0.13.0 just shipped a Kanban + "Tenacity" feature set covering the same problem space we've been working in for over a year — a board-style task hub where independent agents can pick up jobs and process them concurrently, with retry on failure (e.g. browser flake). The question isn't "should we copy them" — it's "what specific mechanisms do they have that solve a real reliability problem we also have, and could be slotted into our flow engine without a rewrite?"

The comparison only makes sense if you understand each side's frame:

| Dimension | Hermes Kanban | UA Task Hub |
|---|---|---|
| **Worker** | Headless `hermes -p <profile>` subprocess on the same host as the dispatcher | Heterogeneous: Simone (heartbeat-driven Claude Code principal), Cody (downstream principal), VP (external runtime), cron jobs, sub-agents |
| **Dispatcher** | Embedded in gateway; runs `dispatch_once()` on a tick | Embedded in gateway via heartbeat sweeps; `dispatch_sweep()` claims N tasks per beat |
| **Termination authority** | Dispatcher OWNS the worker pid → can `SIGTERM` / `SIGKILL` | Dispatcher does NOT own the principal → can only mutate task state, never "kill" a Claude Code session |
| **Persistence** | SQLite WAL (`<root>/kanban.db` per board) | SQLite WAL (`runtime_state.db`, multi-table) |
| **Atomicity** | `BEGIN IMMEDIATE` + CAS on `tasks.status` + `tasks.claim_lock` | Single-transaction claim with re-fetch + assignment row insert |
| **Audit unit** | `task_runs` row per attempt with full handoff metadata | `task_hub_evaluations` (decision audit) + `task_hub_assignments` (claim ledger) |
| **Side-effect protection** | `HallucinatedCardsError` gate (verifies created cards are real) | `_email_side_effects_detected` (halts retry to prevent double-send) + `completion_token` lock |
| **Completion protocol** | Worker MUST call `kanban_complete(...)` or `kanban_block(...)` — clean rc=0 exit without that is a *protocol violation* | No equivalent: Simone declares completion via Task Hub action; no enforced symmetry |

This is not a "both solving the same problem" comparison. It's a "both solving overlapping problems with different constraints" comparison.

---

## 3. State machines, side-by-side

### Hermes task lifecycle (`hermes_cli/kanban_db.py:93`)

```
VALID_STATUSES = {"triage", "todo", "ready", "running", "blocked", "done", "archived"}
```

```
   ┌──────────┐
   │  triage  │  ← created with --triage; awaits human routing
   └────┬─────┘
        │  user routes
        ▼
   ┌──────────┐    parents not done    ┌──────────┐
   │   todo   │ ─────────────────────→ │   todo   │  (recompute_ready loop)
   └────┬─────┘                         └──────────┘
        │  parents all done
        ▼
   ┌──────────┐    claim_task()       ┌──────────┐
   │  ready   │ ────────────────────→ │ running  │ ← claim_lock + claim_expires
   └──────────┘                         └────┬─────┘
        ▲                                    │
        │ release_stale_claims              │ complete_task() → done
        │ detect_crashed_workers (PID gone) │ block_task()    → blocked
        │ enforce_max_runtime (timeout)     │ heartbeat_claim() ─ extends TTL
        │ reclaim_task() (operator-driven)  │ heartbeat_worker() ─ liveness
        │  ↑ each emits a NEW task_runs row │
        │     with outcome + error          │
        │                                    │
        └────────────────────────────────────┘  on retry-eligible failure
                                              with consecutive_failures < limit

                                          ┌──────────────────────────────┐
                                          │ consecutive_failures ≥ limit │
                                          │   → status = blocked          │
                                          │   → outcome = gave_up         │
                                          └──────────────────────────────┘
```

Every claim creates a NEW row in `task_runs` with the claim's PID, lock, TTL, and heartbeat timestamps. Every termination — clean (`completed`), abnormal (`crashed`, `timed_out`, `spawn_failed`), or operator-driven (`reclaimed`) — closes that run with an `outcome` and `metadata`. The task table holds only the *current* state; the `task_runs` table holds the *attempt history*. (`hermes_cli/kanban_db.py:676-727, 832-852`)

### UA Task Hub task lifecycle (`src/universal_agent/task_hub.py:16-30, 1550-1711, 2851-3195`)

```
TASK_STATUSES = {open, in_progress, blocked, needs_review, delegated,
                  pending_review, scheduled, completed, parked, cancelled}
```

```
   ┌──────────┐
   │   open   │ ← producer creates (CSI / heartbeat / proactive / cron / email / VP / dashboard)
   └────┬─────┘
        │  claim_next_dispatch_tasks(limit=N)
        │  + assignment row insert (state='seized')
        ▼
   ┌─────────────┐  finalize_assignments(state="completed", policy="heartbeat")
   │ in_progress │ ────────────────────────────────────────────────────────────→ ┌───────────┐
   │ + assignment │  (no explicit disposition → task to needs_review)            │ completed │
   │  state=seized│                                                               └───────────┘
   └────┬────┬───┘
        │    │  finalize_assignments(state="failed", policy="heartbeat")
        │    │  → if heartbeat_retry_count < limit: → open (immediate requeue, no delay)
        │    │  → if email side-effects detected:    → needs_review
        │    │  → if retry_count >= limit:           → needs_review
        │    │
        │    └─ delegated → VP runtime executes → pending_review
        │
        │  reconcile_task_lifecycle() (gateway startup, on-demand)
        │  → dead session detected → assignment.state='failed'
        │  → reopen logic same as above
        │
        │  _apply_stale_policy() (UA_TASK_STALE_ENABLED, default off)
        │  → stale_missed_cycles ≥ min_cycles AND age ≥ min_age_minutes
        │  → status=parked
        ▼
   ┌─────────────┐
   │ needs_review│ ← human/operator sign-off
   └─────────────┘
```

Differences worth noting up front:

* **We have many more statuses** because we encode delegation lanes (`delegated`, `pending_review`) and human-review gates (`needs_review`) as first-class statuses, not as side-effects of metadata. Hermes has only `blocked` to capture all of those.
* **We have no per-attempt history table.** Each `task_hub_assignments` row tracks one claim, but it doesn't carry the full handoff metadata Hermes' `task_runs` does (no `outcome`, no closing `summary`, no closing `error`). We track decisions in `task_hub_evaluations`, which is the audit ledger for "was this seized or deferred and why" — different concern.
* **We do not own the worker.** Hermes can SIGTERM the worker pid because the dispatcher spawned it. We can only flip the task back to `open` and rely on the principal noticing on its next loop. (For Simone this works because her heartbeat is the loop. For Cody/VP it works because they re-poll Task Hub. There is no "kill the agent" verb in our system.)

---

## 4. Feature parity matrix

| Mechanism | Hermes v0.13.0 | UA Task Hub | Gap? |
|---|---|---|---|
| Atomic claim (CAS) | Yes — `BEGIN IMMEDIATE` + CAS on `claim_lock IS NULL` (`kanban_db.py:1909-1921`) | Yes — single-transaction claim w/ re-fetch (`task_hub.py:1628-1710`) | Equivalent |
| Live concurrency cap | Yes — `max_spawn` counts `running + new` against limit (`kanban_db.py:3678-3684, 3693`) | Yes — caller passes `limit=N` to `claim_next_dispatch_tasks` (`dispatch_service.py:190-223`) | Equivalent (different mechanism, same outcome) |
| Per-task `max_retries` override | **Yes** (`kanban_db.py:608, 3398-3408`) | **No** — only global env vars `UA_TASK_HUB_HEARTBEAT_MAX_RETRIES` / `UA_TASK_HUB_TODO_MAX_RETRIES` | **GAP we could fill** |
| Global retry limit | `DEFAULT_FAILURE_LIMIT = 2` (`kanban_db.py:2808`) | `heartbeat_max_retries=3`, `todo_retry_limit=3` (`task_hub.py:2977-3169`) | Equivalent |
| Unified failure counter (spawn/timeout/crash) | Yes — `consecutive_failures` reset only on success (`kanban_db.py:586, 3340-3491`) | Two separate counters: `heartbeat_retry_count`, `todo_retry_count` per policy (`task_hub.py:2977-3169`) | Different shape but functionally equivalent |
| Auto-block ("dead-letter") on retry exhaustion | Yes — task → `blocked` with `gave_up` event (`kanban_db.py:3410-3457`) | Yes — task → `needs_review` with `*_retry_exhausted` reason (`task_hub.py:2999-3010`) | Equivalent (different state name) |
| Exponential backoff (per-task) | **No** — retry happens immediately on next dispatcher tick | **No** — task is reopened to `open` and immediately eligible | Both lack this |
| Exponential backoff (scheduler-cadence) | Implicit — dispatcher tick interval | **Yes** — `_heartbeat_retry_delay_seconds()` 10s × 2^N capped (`heartbeat_service.py:946-953, 1133-1142`) | We have it where it matters |
| Claim TTL (stale claim release) | Yes — 15min default, `release_stale_claims()` per tick (`kanban_db.py:101, 1993-2037`) | Yes — `release_stale_assignments(stale_after_seconds=1800)` (`task_hub.py:3259-3308`), but NOT auto-called per tick | Mechanism present, **wiring gap** |
| Per-task `max_runtime_seconds` (timeout) | **Yes** — SIGTERM → 5s grace → SIGKILL (`kanban_db.py:3085-3195`) | **No** — only the assignment-level `stale_after_seconds` (manual cron, doesn't kill anything) | **GAP we cannot fill the same way (we don't own the principal)** |
| PID liveness check | Yes — `_pid_alive()` w/ Linux `/proc/<pid>/status` zombie detection (`kanban_db.py:2914-2976`) | **No** — we don't have a PID for our principals; we have session-membership checks | Not applicable |
| Worker exit classification | Yes — `clean_exit` / `nonzero_exit` / `signaled` / `unknown` (`kanban_db.py:2879-2911`) | Not applicable (no subprocess) | Not applicable |
| Protocol-violation gate (clean exit but task still running) | **Yes** — `failure_limit=1` immediate auto-block (`kanban_db.py:3256-3271, 3320-3331`) | **Partial** — completion without disposition → `needs_review` (`task_hub.py:2965-2975`) | **Concept worth adapting** |
| Idempotency on side-effects | `HallucinatedCardsError` (verifies created sub-tasks exist) (`kanban_db.py:2317-2335`) | `_email_side_effects_detected` (halts retry on outbound evidence) + `completion_token` (`task_hub.py:2769-2807, 1641-1648`) | Different scope; both correct for their domain |
| Heartbeat / liveness ping from worker | Yes — `kanban_heartbeat` tool extends TTL + emits event (`kanban_db.py:1962-1990, 3034-3082; tools/kanban_tools.py:464-509`) | **No** — heartbeat *service* exists, but no per-task heartbeat from inside a long-running principal | **Mostly N/A in our model** |
| Operator-driven force-reclaim | Yes — `reclaim_task()` regardless of TTL (`kanban_db.py:2040-2106`); also `reassign_task()` to a different profile | Yes — `perform_task_action(action="reopen"/"park"/"cancel")` | Equivalent |
| Per-attempt history (run table) | **Yes** — `task_runs` carries claim, PID, heartbeat, outcome, summary, metadata, error (`kanban_db.py:832-852, 676-727`) | **No** dedicated runs table — assignments are claim-ledger-only; evaluations are decision audit | **GAP worth filling** |
| DAG / parent-child blocking | Yes — `recompute_ready()` only promotes when ALL parents are `done` (`kanban_db.py:1815-1841`); claim re-checks invariant (`kanban_db.py:1864-1888`) | Parent/child relationships exist (mission envelope → phases) but **no blocking enforcement** | **Subtle gap; not always wrong for our model** |
| Multi-board (project) isolation | Yes — `<root>/kanban/boards/<slug>/kanban.db` per project (`kanban_db.py:11-23`) | We use `project_key` as a logical lane in one DB | Equivalent (different mechanism) |
| Hallucination gate on completion | Yes — `created_cards` ids must verify against `tasks.created_by` (`kanban_db.py:2272-2335`) | We don't surface a "I created sub-task X" claim; sub-tasks are created via `upsert_item` directly | Different model; not a gap |
| Auto-injected lifecycle prompt | Yes — `KANBAN_GUIDANCE` in every worker's system prompt (`agent/prompt_builder.py`) | Yes — `memory/HEARTBEAT.md` for Simone; `cody-implements-from-brief` skill for Cody | Equivalent in spirit |

---

## 5. Five potential adaptations, ranked

I'll be explicit about my recommendation level (1 = "I'd ship this", 5 = "interesting but I'd think twice") so you can use this as the seed for a follow-up implementation plan.

### #1 — Per-task `max_retries` override (RECOMMEND: 1, "ship it")

**What it is:** A nullable `max_retries` integer on each task that, if set, overrides the global `UA_TASK_HUB_HEARTBEAT_MAX_RETRIES` / `UA_TASK_HUB_TODO_MAX_RETRIES` for *this task only*. None (the default) falls through to the global.

**Why we want it:** Some tasks are inherently flakier than others. A "fetch Twitter trending" task in a degraded provider window might deserve `max_retries=5`. A "send a $500 wire transfer" task probably wants `max_retries=1` so we don't spam re-attempts on a network blip. Today both are governed by the same global default.

**Hermes precedent:** `tasks.max_retries` column added in PR #21330. Resolution order in `_record_task_failure` (`kanban_db.py:3398-3408`): per-task override → caller-supplied → DEFAULT.

**UA implementation sketch:** Add `max_retries INTEGER` to `task_hub_items`. In `finalize_assignments` heartbeat/todo policy branches (`task_hub.py:2977-3169`), check `task["max_retries"]` first and override the env var if present. Surface as a creation-time arg on `upsert_item` and as a column in the dashboard drawer.

**Risk:** Low. Additive column, additive resolution order, no behavior change for tasks that don't set it.

**Estimated diff size:** ~50–80 LOC plus one DB migration.

### #2 — Explicit `task_runs` attempt history (RECOMMEND: 2, "high-value, moderate scope")

**What it is:** A new `task_hub_runs` table that holds one row per claim attempt, with `claim_lock`, `started_at`, `ended_at`, `outcome` (`completed` / `failed` / `reclaimed` / `timed_out` / `crashed` / `gave_up`), `summary`, `metadata`, `error`. The `tasks` row carries only the current state and a `current_run_id` pointer; the runs table is the durable attempt ledger.

**Why we want it:** Today we have `task_hub_assignments` (the claim ledger) and `task_hub_evaluations` (the seize-vs-defer decision audit). Neither has the closing `summary` / `error` / `metadata` fields that make Hermes' `build_worker_context` so useful when a downstream task or a human reviewer wants to read the full attempt history. When a Simone task is reopened-and-retried 3 times before going to `needs_review`, today we lose the per-attempt summaries and have to reconstruct them from comments + logs.

**Hermes precedent:** `task_runs` schema (`kanban_db.py:832-852`) + `Run` dataclass (`kanban_db.py:676-727`) + `_end_run()` called from every termination path. Used by `build_worker_context` to feed the next worker.

**UA implementation sketch:** New table, with `_end_run` helper called from every `finalize_assignments` branch. The closing summary/metadata fields would mostly mirror what we already store in `task_hub_assignments.result_summary` plus what we store in metadata.dispatch — the value is making them queryable as a coherent attempt history rather than scattered across two tables.

**Risk:** Moderate. New table, several writers to update, a migration. Not a behavior change but a structural one.

**Estimated diff size:** ~150–250 LOC + migration + tests.

### #3 — Protocol-violation auto-block (RECOMMEND: 2, "good idea, moderate scope")

**What it is:** Detect the case where a principal "completes" a task without making the lifecycle mutation we expect (today: `mission_guardrails.py` calls this "Execution Missing Lifecycle Mutation" and fires post-hoc). Instead of just flagging it post-hoc, treat it as a **protocol violation**: trip the breaker on the first occurrence, park the task in `needs_review` with a clear reason. No retry, because retrying a principal whose loop keeps not closing the task just loops forever.

**Why we want it:** This actually happened — see `services/todo_dispatch_service.py` and the 2026-05-07 fix mentioned in `Documentation_Status.md` ("Item 7 — added FINAL DISPOSITION VERIFICATION block to ToDo prompt"). We solved it via prompt engineering, which is fine but brittle. A code-side gate gives us defense-in-depth.

**Hermes precedent:** `_classify_worker_exit` returns `clean_exit` for rc=0-with-task-still-running, and `detect_crashed_workers` calls `_record_task_failure(failure_limit=1)` on it (`kanban_db.py:3256-3271, 3320-3331`).

**UA implementation sketch:** In `reconcile_task_lifecycle` and in `release_stale_assignments`, when an orphaned `in_progress` task is detected AND there is no failure event AND there are no side-effect deliveries AND the principal's session is alive (not crashed), classify it as a protocol violation and immediately push it to `needs_review` with reason `protocol_violation_no_disposition`. This is harder than Hermes' version because we don't have an exit code — but we have "session was alive but task was never finalized" as a reasonable proxy.

**Risk:** Moderate. False positives are possible (a long-running heartbeat tick that hadn't finalized yet). The fix is to combine session-alive + assignment-is-old (e.g., `started_at < now - 3 * heartbeat_interval`).

**Estimated diff size:** ~100–150 LOC + tests.

### #4 — Auto-call `release_stale_assignments` from each heartbeat sweep (RECOMMEND: 1, "wiring fix, ship it")

**What it is:** We already have `release_stale_assignments(stale_after_seconds=1800)` (`task_hub.py:3259-3308`). It's not in the heartbeat sweep loop. Wire it in.

**Why we want it:** Hermes calls `release_stale_claims()` first thing in *every* `dispatch_once()` tick (`kanban_db.py:3658`). We have the equivalent function but it's not being called in the equivalent place — meaning a stuck assignment quietly stays stuck until an operator runs it manually or until `reconcile_task_lifecycle` runs at gateway startup.

**Important caveat:** This is **not** a kill-the-worker action in our model. It marks the assignment as `state='failed'` and lets the next sweep pick the task back up. That's fine. It's the "release the lock" half of Hermes' equivalent; we don't have the "kill the worker" half because we don't have the worker.

**UA implementation sketch:** In `dispatch_service.dispatch_sweep`, before the claim, call `task_hub.release_stale_assignments(prefix=("heartbeat:", "todo:"), stale_after_seconds=…)`. Make `stale_after_seconds` configurable via env var (`UA_DISPATCH_STALE_AFTER_SECONDS`, default 1800). Important: this also needs to call `reopen_task_if_orphaned` (or equivalent) so the *task* gets back to `open` after the assignment is freed — `release_stale_assignments` today only mutates the assignment row, not the task row.

**Risk:** Low. Existing function, new caller. The one risk is releasing a stale assignment for a session that's actually still busy — but our heartbeat service already tracks busy sessions, so we can pass that set in.

**Estimated diff size:** ~30–60 LOC + tests.

### #5 — Per-task `max_runtime_seconds` (RECOMMEND: 4, "valuable but constrained by our model")

**What it is:** A per-task wall-clock budget. If exceeded, terminate the worker and mark the task as `timed_out`.

**Why we want it (and why we mostly can't):** Hermes can SIGTERM the worker pid and SIGKILL after a 5-second grace window (`kanban_db.py:3085-3195`). We cannot. Our "worker" is a Claude Code principal, a Cron job, or a VP external runtime. We do not own a PID for any of them. The closest analogue would be:

* For **cron jobs**: the cron service already has timeouts. No new work needed.
* For **VP missions**: the VP runtime already has its own timeout machinery. No new work needed.
* For **Simone heartbeat ticks**: the heartbeat service has a `daemon_stuck_run_timeout` mechanism (see `Heartbeat_Service.md`). No new work needed.
* For **Cody / dispatched principals**: we have no kill verb. The best we can do is mark the task as `timed_out` and let the next sweep reclaim it — which is essentially what we already do via `release_stale_assignments`.

**So the value of adding a `max_runtime_seconds` column to `task_hub_items` is really** "make stale_after_seconds *per task* instead of a global cron arg." That's similar to adaptation #1 (`max_retries`) but for time. Some tasks are short (a 2-minute fetch); some are long (a 4-hour migration).

**UA implementation sketch:** Add `max_runtime_seconds` column. In `release_stale_assignments`, instead of a single global threshold, use `COALESCE(task.max_runtime_seconds, default_seconds)` per row. Surface the column on `upsert_item`.

**Risk:** Moderate-low. The caveat is that there's no kill — we just mark the assignment failed and let the principal eventually notice. So the value is "task gets re-routed faster" not "worker gets killed faster." That changes the cost-benefit calculation. Worth doing for the long-task / short-task differentiation; not life-changing.

**Estimated diff size:** ~50–80 LOC + migration.

---

## 6. What I would NOT adopt

* **Hermes' protocol-violation `failure_limit=1` immediate trip on rc=0-without-completion.** We don't have an exit code. The "clean exit" signal would have to be invented from "session was alive AND assignment is old AND no disposition was recorded," which is a softer signal than rc=0. The safer adaptation is #3 above (push to `needs_review`, don't trip an immediate ban).
* **Hermes' PID liveness with `/proc/<pid>/status` zombie detection.** Not applicable — there is no PID for a Claude Code principal session. The session-alive check we already do via `running_session_ids` is the right shape for our model.
* **`HallucinatedCardsError` style "verify created cards on completion."** We don't have the same fan-out problem (we don't have workers regularly creating sub-tasks via a `kanban_create` tool). If we ever add that pattern (e.g., Cody creates child tasks), this becomes relevant. Not today.
* **Auto-injected lifecycle prompt block.** We already do the equivalent via `memory/HEARTBEAT.md` for Simone and per-skill SKILL.md for Cody. The mechanism is different but the outcome is the same.
* **Hermes' `task_runs.metadata` as the canonical handoff context for the next worker.** This is tied to their `build_worker_context` design where one task's output becomes the next task's prompt. We have a different model (Simone reads task descriptions and decides; principals don't read each other's outputs without operator routing). Not a gap.
* **Multi-board isolation as separate DBs.** Our `project_key` lane field is good enough. Splitting into separate DB files per project would break our cross-project Mission Control views.

---

## 7. Three honest acknowledgements about us

While reading their kernel I noticed three things about ours that aren't bugs but are worth naming:

1. **Our retry semantics are policy-conditional, theirs are uniform.** We have `policy="heartbeat"` and `policy="todo"` branches in `finalize_assignments` (`task_hub.py:2977, 3060`) that handle retry differently. Hermes has one `_record_task_failure` that handles every termination type the same way. Both are reasonable; ours is more flexible (different policies for different agent classes), theirs is simpler. If we ever want to add a third policy (e.g., for VP missions), the policy proliferation will start to hurt. Worth keeping an eye on.

2. **Our claim is atomic but not idempotent.** Calling `claim_next_dispatch_tasks` twice with the same task_id in flight returns the task on the first call; the second call's CAS fails (good) but the assignment row was *already inserted* before the CAS fail check (line 1640-1670, the order is "INSERT assignment THEN UPDATE task with CAS"). Hermes inserts the run row only AFTER a successful CAS (`kanban_db.py:1922-1953`). If we ever see double-inserted assignments, this is the place to look.

3. **We have no `route_all_to_simone` dedup.** `agent_router.py:35-55` adds `_routing` to every claimed task without checking if it's already routed. Calling it twice on the same list creates two `_routing` keys. Not a bug today (single caller), but a footgun if a future caller routes through twice.

None of these are urgent. Capturing them so a future plan can decide whether to fold them in.

---

## 8. Recommended next step (if Kevin wants to proceed)

If we like the adaptations in §5, the natural next step is a **scoped implementation plan** covering #1, #4, and #2 in that order — they're additive, low-risk, and each delivers value independently:

1. **Phase A** (small, ~1 PR): per-task `max_retries` column + resolution order + dashboard surface.
2. **Phase B** (small, ~1 PR): wire `release_stale_assignments` into `dispatch_sweep` with proper `running_session_ids` filtering.
3. **Phase C** (medium, ~1 PR): `task_hub_runs` table + close-on-finalize wiring + minimal dashboard "attempt history" view.

#3 (protocol-violation gate) and #5 (per-task max_runtime) would come later as "Phase D / E" once #1 and #2 prove the additive shape works.

I have NOT written a plan; this report is intentionally just the comparison.

---

## 9. Technical Appendix A — UA Task Hub anatomy

**Source-of-truth files:**

| Component | File |
|---|---|
| Core task lifecycle | `src/universal_agent/task_hub.py` |
| Dispatch sweep | `src/universal_agent/services/dispatch_service.py` |
| Agent router (Simone-first) | `src/universal_agent/services/agent_router.py` |
| Idle dispatch loop | `src/universal_agent/services/idle_dispatch_loop.py` |
| ToDo dispatch | `src/universal_agent/services/todo_dispatch_service.py` |
| Heartbeat service (scheduler-cadence backoff lives here) | `src/universal_agent/heartbeat_service.py` |
| DB path resolution | `src/universal_agent/durable/db.py` |

**Statuses (`task_hub.py:16-30`):**

```
TASK_STATUS_OPEN, TASK_STATUS_IN_PROGRESS, TASK_STATUS_BLOCKED,
TASK_STATUS_REVIEW (= "needs_review"), TASK_STATUS_DELEGATED,
TASK_STATUS_PENDING_REVIEW, TASK_STATUS_SCHEDULED,
TASK_STATUS_COMPLETED, TASK_STATUS_PARKED, TASK_STATUS_CANCELLED
TERMINAL_STATUSES = {completed, parked, cancelled}
```

**Schema tables (`task_hub.py:199-332`):**

* `task_hub_items` — task metadata, status, score, agent_ready, labels, source_kind, metadata_json, completion_token
* `task_hub_assignments` — one row per claim, state ∈ {seized, running, completed, failed, reconciled}, started_at, ended_at, agent_id, result_summary
* `task_hub_evaluations` — decision audit (seize / defer + reason)
* `task_hub_dispatch_queue` — ranked snapshot rebuilt each sweep
* `task_hub_comments`, `task_hub_question_queue`, `task_hub_workstreams`, `task_hub_notifications`, `task_hub_delivery_evidence`

**Claim flow (`task_hub.py:1550-1711`):**

1. `rebuild_dispatch_queue()` scores all non-terminal tasks
2. Filter: `status ∈ {open, needs_review}` AND `agent_ready=True` AND `score ≥ UA_TASK_AGENT_THRESHOLD` (default 3)
3. Filter: not in `forbidden_source_kinds`; trigger type matches if specified
4. For each candidate up to `limit`:
   * Re-fetch fresh row
   * Check `completion_token` (skip if locked)
   * INSERT assignment with `state='seized'`
   * UPDATE task to `in_progress` + `seizure_state='seized'`
   * Record evaluation (`decision='seize'`)
   * Update `metadata.dispatch` with `active_assignment_id`, `active_agent_id`
5. Single commit

**Retry policies (`task_hub.py:2977-3169`):**

```
policy="heartbeat":
  if state != "completed":
    heartbeat_retry_count += 1
    if count >= heartbeat_retry_limit:    → status=needs_review, reason="heartbeat_retry_exhausted"
    elif _email_side_effects_detected:    → status=needs_review, reason="heartbeat_retryable_with_side_effects"
    else:                                  → status=open, reason="heartbeat_<state>_retryable"
  elif no explicit disposition:           → status=needs_review (waiting on human)

policy="todo":
  if can_self_verify_after_delivery:     → status=completed (auto), set completion_token
  elif _email_side_effects_detected:     → status=needs_review
  else:
    todo_retry_count += 1
    if count >= todo_retry_limit:        → status=needs_review, reason="todo_retry_exhausted"
    else:                                 → status=open, reason="todo_<state>_retryable"

policy="legacy":  fall-through, no auto-retry handling
```

**Stale policy (opt-in, `task_hub.py:1314-1348`, default disabled):**

* `UA_TASK_STALE_ENABLED=0`
* `UA_TASK_STALE_MIN_CYCLES=4`
* `UA_TASK_STALE_MIN_AGE_MINUTES=180`
* Increments `metadata.stale_missed_cycles` per skipped queue rebuild; parks task when both thresholds exceeded.

**Reconciliation (`task_hub.py:2621-2849`, called at gateway startup + on demand):**

* For each `in_progress` task: if assignment is `seized`/`running` and session is dead → mark assignment failed, then route by side-effects / VP-mission backfill / retry-count to `delegated` / `needs_review` / `open`.
* For each `completed` task with auto-completion reason: re-flag to `needs_review` for verification.

**Stale assignment release (`task_hub.py:3259-3308`, manual):**

```python
release_stale_assignments(
    agent_id_prefix=("heartbeat:",),    # or list
    stale_after_seconds=1800,           # 30 min default
    limit=500,
)
# Marks seized/running assignments older than threshold as state='failed'
# Does NOT reopen the task → orphan recovery happens at next reconcile
```

Not currently called inside `dispatch_sweep`. **This is the wiring gap noted in §5 #4.**

**Heartbeat-cadence backoff (this is where we DO have backoff, `heartbeat_service.py:946-953`):**

```python
def _heartbeat_retry_delay_seconds(attempt, base_seconds, max_backoff_seconds):
    bounded_attempt = max(1, min(attempt, 12))
    return min(base_seconds * (2 ** (bounded_attempt - 1)), max_backoff_seconds)
```

Used when a heartbeat tick is busy or fails — the *next tick* is delayed 10s × 2^(N-1), capped. Tested in `tests/unit/test_heartbeat_retry_queue.py` ("test_process_session_busy_retry_uses_exponential_backoff", "test_run_heartbeat_failure_schedules_exponential_retry"). This is at the **scheduler-cadence layer**, not the task-reopen layer.

**Side-effect protection (`task_hub.py:2769-2807`):**

```python
def _email_side_effects_detected(conn, task_id):
    # Returns True if delivery_evidence rows show the task already produced
    # outbound side effects (message_id, thread_id). Halts retry.
```

Plus `completion_token` lock (`task_hub.py:1641-1648`): once set, the task cannot be re-claimed.

---

## 10. Technical Appendix B — Hermes v0.13.0 Kanban + Tenacity anatomy

**Repo:** [github.com/NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — release notes: `RELEASE_v0.13.0.md`.
**Source-of-truth files:**

| Component | File |
|---|---|
| Persistence + dispatcher kernel | `hermes_cli/kanban_db.py` (~4660 LOC) |
| CLI surface | `hermes_cli/kanban.py` |
| Worker-facing tools | `tools/kanban_tools.py` |
| Diagnostics | `hermes_cli/kanban_diagnostics.py` |
| Auto-injected worker prompt | `agent/prompt_builder.py` (KANBAN_GUIDANCE block) |
| Worker / orchestrator skills | `skills/devops/kanban-worker/`, `skills/devops/kanban-orchestrator/` |

**Statuses (`kanban_db.py:93`):**

```
VALID_STATUSES = {"triage", "todo", "ready", "running", "blocked", "done", "archived"}
```

**Run outcomes (`kanban_db.py:847-848`):**

```
completed | blocked | crashed | timed_out | spawn_failed | gave_up | reclaimed
```

**Schema (`kanban_db.py:753-881`):**

* `tasks` — current state, claim_lock, claim_expires, worker_pid, last_heartbeat_at, current_run_id, consecutive_failures, last_failure_error, max_runtime_seconds, max_retries, idempotency_key, workspace_kind
* `task_runs` — one row per claim attempt with full claim/PID/heartbeat/outcome/summary/metadata/error
* `task_links` — parent → child for DAG
* `task_comments`, `task_events`, `kanban_notify_subs`

Indexes on `(assignee, status)`, `(status)`, `(tenant)`, `(idempotency_key)`, plus run/event indexes for fast dashboard queries.

**Dispatcher tick (`dispatch_once`, `kanban_db.py:3587-3760`):**

```
1. Reap zombie children: while waitpid(-1, WNOHANG) → record exit status by pid
2. release_stale_claims(conn) — TTL-based reaper
3. detect_crashed_workers(conn) — PID liveness check + zombie-state detection;
   classifies clean_exit / nonzero_exit / signaled / unknown
4. enforce_max_runtime(conn) — SIGTERM → 5s grace → SIGKILL
5. recompute_ready(conn) — promote todo→ready when all parents are done
6. Live concurrency cap: count tasks already running, skip if running + spawned ≥ max_spawn
7. For each ready row ordered by priority DESC, created_at ASC:
   - Skip non-spawnable assignees (control-plane lanes that aren't real profiles)
   - claim_task() — atomic CAS
   - resolve_workspace() — scratch / worktree / dir
   - spawn_fn(claimed, workspace, board) → pid
   - _set_worker_pid()
```

**Atomic claim (`claim_task`, `kanban_db.py:1848-1959`):**

```sql
UPDATE tasks
   SET status        = 'running',
       claim_lock    = ?,
       claim_expires = ?,
       started_at    = COALESCE(started_at, ?)
 WHERE id = ?
   AND status = 'ready'
   AND claim_lock IS NULL
```

If `cur.rowcount != 1`: someone else won. Otherwise insert `task_runs` row, update `current_run_id`. **Pre-claim invariant guard:** if any parent is not `done`, demote to `todo` and emit `claim_rejected`.

**Stale claim release (`release_stale_claims`, `kanban_db.py:1993-2037`):**

```sql
SELECT id, claim_lock, worker_pid FROM tasks
 WHERE status = 'running' AND claim_expires < ?
```

For each: `_terminate_reclaimed_worker()` (best-effort SIGTERM by pid), then UPDATE the task to `ready` (with claim_lock check to avoid stomping a fresh claim), close run as `outcome='reclaimed'`.

**PID liveness + zombie detection (`_pid_alive`, `kanban_db.py:2914-2976`):**

* POSIX: `os.kill(pid, 0)` succeeds → process exists. Then peek `/proc/<pid>/status` for `State: Z` (Linux) or `ps` BSD `stat` field for `Z` (macOS) to disambiguate "alive" vs "zombie post-exit pre-reap."
* Windows: `OpenProcess` + `WaitForSingleObject` (NEVER `os.kill(pid, 0)` — it's `CTRL_C_EVENT` on Windows, bpo-14484).

**Worker exit classification (`_classify_worker_exit`, `kanban_db.py:2879-2911`):**

```
clean_exit   = WIFEXITED && WEXITSTATUS == 0 → protocol violation if task still running → failure_limit=1
nonzero_exit = WIFEXITED && WEXITSTATUS != 0 → real error
signaled     = WIFSIGNALED                    → real crash (OOM/SIGKILL)
unknown      = pid not in reap registry        → fall back to default counter
```

The reap registry is bounded (TTL 600s, max 4096 entries) with both age-based and size-based trim.

**Per-task timeout (`enforce_max_runtime`, `kanban_db.py:3085-3195`):**

```
For each running task with max_runtime_seconds set:
  - Check claim is host-local (lock starts with this host's _claimer_id)
  - elapsed = now - active_started_at  # measured from current run, NOT task creation
  - if elapsed >= limit:
      kill(pid, SIGTERM)
      poll for ≤ 5s, 0.5s interval
      if still alive: kill(pid, SIGKILL)
      Update task → ready (clearing claim/expires/pid/heartbeat)
      Close run with outcome='timed_out'
      Emit timed_out event
      Increment consecutive_failures via _record_task_failure (may trip breaker)
```

Crucially, the elapsed measurement is from `task_runs.started_at` (the current attempt), not `tasks.started_at` (the first ever attempt). Retries get a fresh budget.

**Unified failure counter (`_record_task_failure`, `kanban_db.py:3340-3491`):**

```
Threshold resolution:
  1. tasks.max_retries (per-task override)
  2. caller-supplied failure_limit (gateway passes config)
  3. DEFAULT_FAILURE_LIMIT = 2

If failures >= effective_limit:
  → status = blocked
  → emit gave_up event with payload {failures, effective_limit, limit_source, error, trigger_outcome}
Else:
  → status = ready (or stays at ready/running depending on caller)
  → counter incremented
```

Reset to 0 only by `_clear_failure_counter` from `complete_task` on success.

**Heartbeat (`heartbeat_claim` + `heartbeat_worker`, `kanban_db.py:1962-1990, 3034-3082`):**

```
heartbeat_claim: extends claim_expires (CAS-guarded by claim_lock). Used to prevent
release_stale_claims from reclaiming a worker that's still actually working.

heartbeat_worker: writes last_heartbeat_at on tasks AND task_runs, emits heartbeat
event. Orthogonal to claim TTL — gives the dispatcher a liveness signal even when
the worker has forked a long-lived child whose Python is alive but the work is
actually stuck.
```

Worker-facing tool `kanban_heartbeat` (`tools/kanban_tools.py:464-509`) calls BOTH — extends the TTL and records the event in one call. The tool docstring explicitly warns about the trap of recording events without extending the TTL.

**Hallucination gate on completion (`complete_task`, `kanban_db.py:2272-2335`):**

```python
if created_cards:
    verified, phantom = _verify_created_cards(conn, task_id, created_cards)
    if phantom:
        emit completion_blocked_hallucination event
        raise HallucinatedCardsError(phantom, task_id)
```

Plus an advisory post-completion scan of summary+result for `t_<hex>` references that don't resolve, emitted as `suspected_hallucinated_references` event (non-blocking).

**Worker tools registered only when `HERMES_KANBAN_TASK` env var set (or profile has `kanban` toolset, `tools/kanban_tools.py:46-73`):**

* `kanban_complete(summary=, metadata=, result=, created_cards=)`
* `kanban_block(reason=, summary=, metadata=)`
* `kanban_heartbeat(note=)`
* `kanban_comment(task_id=, body=)`
* `kanban_create(title=, assignee=, parents=, …)` (orchestrator only)
* `kanban_list`, `kanban_unblock` (orchestrator only)

A normal `hermes chat` session sees ZERO kanban tools — the surface is gated by the dispatcher's env injection. (`tools/kanban_tools.py:1-25`)

**Auto-injected lifecycle prompt:** `KANBAN_GUIDANCE` block in `agent/prompt_builder.py` is added to every dispatched worker's system prompt. Covers the 6-step lifecycle (orient → work → heartbeat → block/complete) and the "decompose, don't execute" anti-temptation rules. The skill files (`kanban-worker/SKILL.md`, `kanban-orchestrator/SKILL.md`) are the deeper playbook loaded explicitly via `--skills`.

---

## 11. Sources & verification

* Hermes v0.13.0 release notes: `https://github.com/NousResearch/hermes-agent/blob/main/RELEASE_v0.13.0.md`
* Hermes repo (cloned shallow at investigation time, May 2026): `/tmp/hermes-agent` (102 MB, depth=50)
* Specifically read in full or in part:
  * `hermes_cli/kanban_db.py` — schema, dispatcher, retry, reaper, heartbeat, hallucination gate
  * `tools/kanban_tools.py` — worker-facing tool surface
  * `skills/devops/kanban-orchestrator/SKILL.md` — orchestrator playbook
  * `skills/devops/kanban-worker/SKILL.md` — worker pitfalls
  * `RELEASE_v0.13.0.md` — release notes (full)
* UA side mapped via Explore subagent reading:
  * `src/universal_agent/task_hub.py`
  * `src/universal_agent/services/dispatch_service.py`
  * `src/universal_agent/services/agent_router.py`
  * `src/universal_agent/services/todo_dispatch_service.py`
  * `src/universal_agent/heartbeat_service.py`
  * `src/universal_agent/durable/db.py`
  * `tests/unit/test_heartbeat_retry_queue.py` (confirmed exponential backoff lives at the heartbeat-cadence layer, not task-reopen layer)
* Cross-referenced with: `docs/02_Subsystems/Heartbeat_Service.md`, `docs/02_Subsystems/Task_Hub_Dashboard.md`, `docs/proactive_signals/claudedevs_intel_v2_design.md`, `Documentation_Status.md` § 2026-05-07 entry on FINAL DISPOSITION VERIFICATION.

Every claim about UA code in this report is anchored to a `file:line` reference in §9. Every claim about Hermes code is anchored to a `file:line` reference in §10. The intent is that a follow-up implementation plan can quote these references directly without re-doing the investigation.

---

## 12. Open questions / things I deliberately didn't investigate

* **Performance characteristics of Hermes' `dispatch_once`** under high task volume — how many tasks/sec does their CAS+reaper handle? I read the code but did not benchmark.
* **What happens to `task_runs` rows on `archived` status** — are they retained forever or pruned? Did not chase.
* **Whether the `consecutive_failures` counter's "per-task per-profile" scoping (release-notes phrasing) actually means anything different from "per-task"** — in code I only saw per-task. Worth a follow-up read if we adopt #1.
* **The exact Hermes dispatcher tick interval and how it's scheduled** — embedded in their gateway, didn't trace fully.
* **The Hermes `kanban_diagnostics.py` module (650 LOC)** — looks like a system-health surface; did not read in detail.
* **The Hermes web/dashboard side** under `plugins/kanban/dashboard/` — purely UI; outside the scope of this comparison.

These are all "nice to know if we're going to ship the adaptations" — not "blocks the report."

---

# Section 13 — Reframing addendum (2026-05-10, post-discussion)

This section was added after a follow-up discussion with Kevin clarified two things the original report got partially wrong, and added context about agent capability asymmetry that materially changes how the §5 ranking should be read.

## 13.1 — Verified `needs_review` semantics (corrects the original report)

The original §1 framed UA as "optimizes for human-judged completion." In a mid-discussion follow-up I walked that back to "needs_review is just a flag, still autonomously re-dispatchable by Simone." **The walk-back was wrong.** Direct code reading shows the original §1 was closer to reality. Three independent gates make `needs_review` a hard human-gated state today:

**Gate 1 — Default ineligibility** (`src/universal_agent/task_hub.py:1388-1393`):

```python
elif status == TASK_STATUS_REVIEW:
    eligible = bool(item.get("agent_ready")) and is_system_schedule
```

A regular task in `needs_review` is `eligible = False` unless it's a `system_schedule` (cron-generated operator directive).

**Gate 2 — Heartbeat / todo anti-starvation** (`task_hub.py:1402-1408`):

```python
if status == TASK_STATUS_REVIEW:
    reason = str(dispatch_meta.get("last_disposition_reason") or "")
    if reason.startswith("heartbeat_") or reason.startswith("todo_retry"):
        eligible = False
```

Any task that landed in review via a heartbeat or todo failure path is hard-blocked from re-dispatch. The set of reasons that trip this gate, from `finalize_assignments` (`task_hub.py:2977-3169`):

* `heartbeat_completed_without_disposition` — protocol violation
* `heartbeat_retry_exhausted` — retry budget hit
* `heartbeat_retryable_with_side_effects` — idempotency safety on outbound delivery
* `todo_retry_exhausted` — todo retry budget hit
* `todo_retryable_with_side_effects` — same as above for todo

**Gate 3 — Persistent todo counter** (`task_hub.py:1410-1418`):

```python
if eligible and status in {TASK_STATUS_OPEN, TASK_STATUS_REVIEW}:
    _retry_count = _safe_int(_dispatch_meta.get("todo_retry_count"), 0)
    _retry_limit = _safe_int(_dispatch_meta.get("todo_retry_limit"), 3)
    if _retry_count >= _retry_limit:
        eligible = False
```

Even if an operator flips the status back to `OPEN`, if `todo_retry_count >= todo_retry_limit`, the task remains gated. The counter persists across status transitions.

**The claim filter at `task_hub.py:1638`** (`status NOT IN {open, needs_review} → skip`) IS permissive — it would let `needs_review` tasks through. But the **upstream queue eligibility filter** at line 1610-1624 (`WHERE q.eligible = 1`) drops the task before it ever reaches the claim filter.

**What overrides exist:**

| Override | Mechanism | What it bypasses | What it doesn't |
|---|---|---|---|
| `must_complete=True` at creation | `task_hub.py:1395-1397` | Gate 1 (default ineligibility) | Gate 3 (todo counter persists) |
| `is_system_schedule` for cron directives | `task_hub.py:1399-1400` | Gate 1 | Gate 3 sometimes |
| Operator `perform_task_action(action="unblock")` | `task_hub.py:4019-4020` | Status flips OPEN | Gate 3 still bites for todo retries; first failure re-trips heartbeat |
| No "reopen-and-reset-counters" action exists | `VALID_ACTIONS` at `task_hub.py:33` | — | To restart a retry-exhausted task autonomously, an operator must manually edit `metadata.dispatch.todo_retry_count` or `heartbeat_retry_count` to 0 |

**Bottom line:** Simone has NO programmatic way to take a `needs_review` task and decide on her own to retry, redirect, or fix it. She can see it in the dashboard but cannot act on it without operator intervention. The system today is more human-gated than Kevin's original design intent called for. The likely history is that each gate was added defensively after some incident (anti-starvation for retry loops, side-effect protection for double-sends, counter persistence for safety) — each one in isolation is reasonable, but together they form a hard barrier the intent never expressed.

## 13.2 — The autonomy gap (intent vs. code)

| Dimension | Design intent | Code today |
|---|---|---|
| Happy path | Autonomous | Autonomous ✓ |
| Transient failure (retry under limit) | Autonomous retry | Autonomous reopen to OPEN ✓ |
| Retry budget exhausted | Simone re-evaluates, decides to retry / redirect / fix / abandon / escalate | Hard human gate (Gate 2) |
| Side-effects detected on retry | Simone judges what was completed and what wasn't, decides whether to ack-complete or retry the un-done part | Hard human gate (Gate 2) |
| Protocol violation (clean run, no disposition) | Simone re-prompts or re-dispatches with corrective context | Hard human gate (Gate 2) |
| Auto-completion reconciled | Simone verifies completion quality, ack-completes or re-dispatches | Hard human gate (Gate 2) |
| True hard failure (unfixable) | Operator escalation | Hard human gate (correct destination, but every above path falls here too) |

**The principle the code violates is the LLM-Native Intelligence Design rule from CLAUDE.md:**

> Code gates and protects execution… LLMs synthesize meaning. When the corpus is bounded enough… let the LLM infer themes, neglected opportunities, recurring blockers, and recommended actions from the evidence.

Each "needs_review" routing is a *deterministic code decision* that the failure context is too risky for any agent to handle. But the failure context — last error, retry count, side-effect evidence, source kind, task description — is exactly the kind of bounded corpus an LLM judge could read and decide on. The current code path doesn't even give Simone a chance.

**The "unstick Simone" sub-recommendation** (call this idea #0, prerequisite to most of §5):

1. Add a new action `rehydrate` to `VALID_ACTIONS` that flips status `needs_review` → `open` AND resets `heartbeat_retry_count` / `todo_retry_count` / `last_disposition_reason` to clean values. Operator-callable from the dashboard.
2. Add a NEW Simone-callable verb (could be a new action `re_evaluate` or could be the existing `seize` extended) that Simone can invoke on a `needs_review` task to take it on for autonomous judgment. The verb's preconditions would still gate dangerous cases (e.g., side-effect-on-irreversible-action), but for the recoverable cases, Simone gets a programmatic on-ramp.
3. Surface the per-failure context (retry history, last error, side-effect evidence) to Simone in her prompt when she does claim a re-evaluated task, so she's judging from evidence, not from task title alone.

This is the missing piece that turns `needs_review` from "human gate" into Kevin's intended "lifeline." It's a small change (one new action handler + one new claim path), and it unlocks the larger autonomy story.

## 13.3 — Agent capability matrix (context for adaptations)

The §5 ranking assumed uniform agent capability. The reality is heterogeneous, and the asymmetry materially changes the calculus for failure-judgment routing.

| Agent | Model (current) | Orchestration burden | Reasoning ceiling | Concurrency / agent-teams | Cost per call |
|---|---|---|---|---|---|
| **Simone** | ZAI/GLM (cheap) | High — full orchestrator with all dispatch tools | Lower (weaker model + distraction from routing) | Single-agent loop | Cheap |
| **Atlas** | TBD — needs verification | Low — specialist | TBD (likely same model class as Simone) | TBD | Cheap (assumed) |
| **Cody (today)** | ZAI/GLM | None — pure executor | Lower | Single-agent subprocess | Cheap |
| **Cody (potentially)** | Anthropic Max | None | **Highest** — Opus/Sonnet 4.x | **Full agent teams + concurrency** via Anthropic SDK | Expensive per call but parallel-capable |

Three implications:

**(a) Simone-as-judge is the *weakest* potential judge in the room if she stays on ZAI.** She wins today only because she's the orchestrator and has accumulated context. For complex failure judgments (was a side-effect partial / complete, was a crash structural / transient, should this task be redirected to a different specialist), a stronger model would judge better. So "Simone judges everything on failure" is a useful default but not the right ceiling.

**(b) Capability-aware escalation is the right shape, not a single judge.** A tiered model:
* **Tier 0** — Code-deterministic re-dispatch for clearly-transient failures (retry under limit, no side-effects). No LLM involved. (We have this today via the reopen-to-OPEN path.)
* **Tier 1** — Simone judges from failure context. Cheap, has orchestration context, sees the whole queue. Most failure judgments live here.
* **Tier 2** — Smarter-judge escalation when Tier 1 fails OR when the failure category is high-stakes. Today there is no Tier 2 (we have only Tier 0 + dashboard surface). The natural Tier 2 is Cody-on-Anthropic (if available) or a one-shot Sonnet/Opus call invoked specifically for the judgment.
* **Tier 3** — Human escalation. Should be rare, reserved for genuinely irreversible / strategic / ambiguous-after-LLM-judgment cases.

**(c) Per-task `max_retries` (recommendation #1 in §5) should be model-aware.** A flaky web-scrape on Simone-ZAI deserves `max_retries=5` because each retry is cheap and the model class makes transient failures plausible. The same task on Cody-Anthropic deserves `max_retries=1` or `2` because each attempt is expensive AND Anthropic-class quality means a first-attempt failure is more likely to indicate a structural problem than a transient blip. The retry budget should be a function of `(task_class, executor_model_class)`, not a single global default.

## 13.4 — Cody-on-Anthropic future-thinking (DO NOT IMPLEMENT — REMEMBER ONLY)

Kevin flagged this as a future-thinking note. Capturing it here so a future implementation plan reads it before designing routing rules.

**The idea:** Cody could be invoked using the Anthropic Max plan / Claude Code direct endpoint via the redefined mapping in `claude_with_mcp_env.sh` (or analogous launcher), instead of the ZAI proxy. Today Cody runs on ZAI by default, same as Simone. Moving Cody to Anthropic would:

1. Make Cody a **stronger coding executor than Simone** (Opus/Sonnet 4.x vs GLM). This inverts the typical hierarchy where the orchestrator is smarter than the executor.
2. Give Cody **agent teams and high concurrency**. Anthropic SDK supports parallel sub-agent invocation; the ZAI proxy is constrained. A Cody-on-Anthropic task could fan out internally to multiple sub-tasks running in parallel — without going through Task Hub at all. That's a fundamentally different shape than the per-subprocess single-agent worker Hermes ships, and actually better on the concurrency axis.
3. Cost more per call but **be parallel-capable**, so the cost analysis is not just "per call" but "per unit of throughput on a complex task."

**Implications for the eventual implementation plan (do not implement, just remember):**

* **Delegation rules need a capability axis.** Simone deciding "delegate to Cody" today is essentially routing within the same model class. Simone deciding "delegate to Cody-on-Anthropic" is routing UP the model class. The cost-benefit changes: Simone should reserve Cody-on-Anthropic for tasks where the reasoning ceiling matters, not just "tasks Cody can do."
* **Concurrency cap interpretation changes.** If Cody-on-Anthropic can internally fan out 5 sub-agents, then `claim_next_dispatch_tasks(limit=1)` claiming one Cody task is *not* the same as claiming one ZAI task — it's claiming one Anthropic-team task, which has 5x the internal concurrency. The Task Hub-level cap might need to be aware of this, or we might decide it's not Task Hub's concern (the team-internal concurrency is bounded by Anthropic's rate limits, not ours).
* **Failure judgment escalation has a natural Tier 2 destination.** If Cody-on-Anthropic exists, the §13.3 Tier 2 ("smarter judge") has a concrete implementation: Simone tags a stuck task `needs_orchestrator_review`, Cody-on-Anthropic claims it specifically for judgment (not execution), reads the failure context, decides retry / redirect / decompose / escalate / abandon. Cheap-LLM-first, expensive-LLM-when-needed.
* **Cody-on-Anthropic might be the right home for Hermes-style agent-teams worker patterns.** Hermes' `kanban_create` lets a worker fan out children. We could let Cody-on-Anthropic do the same INSIDE one Anthropic session via SDK fan-out, without going through Task Hub. That bypasses Task Hub for internal team coordination, which is actually correct (Task Hub is for inter-principal coordination, not intra-team).

**What to NOT do because of this idea:**

* Don't build a Task Hub-level "team concurrency cap" yet. We don't know the model assignment.
* Don't bake Anthropic-vs-ZAI capability assumptions into the retry budget logic before we know which mode Cody runs in.
* Don't assume Simone is the only judge. Section 13.3's tiered model is the right shape even before Cody-on-Anthropic ships.

## 13.5 — Sixth adaptation idea: capability-aware routing on failure (NEW)

Adding this to the §5 list explicitly because §13.3 makes it a separate concern from the original five.

**What it is:** When a task fails, the decision of (retry on same agent / re-route to a stronger agent / escalate to human) depends on (a) what failed, (b) the executor's model class, (c) the failure category (transient vs structural vs side-effect-laden), (d) whether a stronger judge is available.

**Why it's separate:** None of the original five §5 ideas address the *routing* of failures, only the *recovery* of failures on the same lane. This idea is the missing routing layer.

**Implementation sketch (gated on Cody-on-Anthropic landing first OR on a one-shot Sonnet/Opus judge being available):**

1. `finalize_assignments` records the failure context: error string, retry count, side-effect evidence, task source_kind.
2. A new helper `_classify_failure_for_routing(task, context) -> Tier` returns one of: `RetryHere`, `EscalateToStrongerJudge`, `EscalateToHuman`. The classifier could itself be deterministic for clear cases and LLM-judged for ambiguous cases.
3. For `RetryHere`: existing reopen-to-OPEN path.
4. For `EscalateToStrongerJudge`: status becomes `needs_orchestrator_review` (new state, NOT `needs_review` which stays human-only). Eligible for a Cody-on-Anthropic claim specifically for judgment.
5. For `EscalateToHuman`: status `needs_review`, with the failure context attached to the dashboard surface.

**Risk:** This is a real shape change, not a wiring tweak. Worth doing only after the prerequisites land (Cody-on-Anthropic availability, the §13.2 "unstick Simone" rehydrate action, the §5 #2 attempt-history table to feed the judgment with rich context).

**Estimated position in roadmap:** After §5 #1 (per-task max_retries) and #2 (task_runs table), this becomes the natural next step. Probably Phase D or E in the eventual implementation plan.

## 13.6 — Revised §5 ranking through the capability lens

The original §5 ranking assumed uniform capability. Re-ranking with §13.3's tiered-judge model and §13.2's "unstick Simone" prerequisite:

| Rank | Idea | Lens-adjusted rationale |
|---|---|---|
| **0** (prerequisite) | **`rehydrate` action + Simone-callable re-evaluate verb** (§13.2) | Without this, every Hermes adaptation slots into a system that still hard-gates failures. Ship this first. ~30-50 LOC. |
| **1** | Per-task `max_retries` override (original §5 #1) | Becomes more valuable in a model-aware world (§13.3.c). Same scope as before. |
| **2** | Wire `release_stale_assignments` into `dispatch_sweep` (original §5 #4) | Unchanged. Small wiring fix. |
| **3** | Explicit `task_runs` attempt-history table (original §5 #2) | Now also feeds the failure-context input for §13.5's tiered judge. Higher value than originally framed. |
| **4** | Owned-worker Hermes-style reliability for Cody / ToDo dispatch / cron (was §5 #5 partially) | Specific target: the subprocess subset we DO own. PID liveness, exit-code classification, `max_runtime_seconds` with SIGTERM/SIGKILL. Excludes Simone heartbeat (unowned). |
| **5** | Protocol-violation gate (original §5 #3) | Today's `heartbeat_completed_without_disposition` is already a defacto protocol-violation auto-block. The needed work is opening up the *recovery* path (covered by Rank 0), not adding more detection. **Demoted.** |
| **6** | Capability-aware routing on failure (§13.5) | Depends on Cody-on-Anthropic AND on Rank 0–3 landing first. Future phase. |

## 13.7 — Things we still need to verify

For Atlas specifically (Kevin flagged uncertainty):

* What model class is Atlas assigned to? (`grep -r "atlas" config/` or wherever model assignments live)
* What's Atlas's tool surface — does it include Task Hub mutation verbs, or is it observer-only?
* Is Atlas heartbeat-driven (long-lived daemon) or invoked-per-task (subprocess we'd own)?

If Atlas is observer-only or has a narrower tool surface than Simone, it's not a judge candidate but it could be a useful *escalation reader* — surface failure summaries to Atlas for second-opinion, but don't let it mutate. That's a different shape than the tiered judge in §13.3 and worth noting.

For Cody on Anthropic:

* Confirm the launcher mapping that would make Cody invoke `claude` under the Max-plan profile rather than ZAI. (`scripts/claude_with_mcp_env.sh` is one entry point — does it currently force a specific routing for Cody?)
* Confirm the Anthropic SDK agent-team primitives we'd actually use. Concurrency on what axis — parallel tool calls, parallel sub-agent invocations, both?

These verifications are NOT blocking the report; they're inputs to the eventual implementation plan.

---

**Section 13 summary:** The original report's §1 framing was correct; my mid-discussion walk-back was wrong. `needs_review` IS a hard human-gated state today, gated by three independent eligibility filters, and Simone has no programmatic path to act on a reviewed task. Closing that gap is the prerequisite for every other Hermes adaptation to deliver autonomy value — otherwise we'd be making a system that recovers faster but still escalates to humans by default. Add the `rehydrate` action and Simone re-evaluate verb as Rank 0, then proceed with the §5 ideas re-ranked through the agent-capability lens. Remember Cody-on-Anthropic as a future-state assumption that changes delegation, concurrency, and tiered-judge design — but don't implement against it yet.

---

# Section 14 — Final discussion summary (2026-05-10)

This section captures the closing decisions from the discussion, the verified Atlas dispatch finding, the concurrency-layer distinction for Cody, the new visibility requirement, and the final ranked near-term plan that the phased plan doc (`hermes-adaptation-phased-plan-2026-05-10.md`) implements.

## 14.1 — Atlas throttling: confirmed architectural, not capacity

Verified the dispatch path in code:

* Only Simone has a heartbeat-driven claim loop. `claim_next_dispatch_tasks` is called from Simone's heartbeat sweep (`services/dispatch_service.py`), the ToDo dispatch service, and the idle dispatch loop — all of which run AS PART OF Simone's heartbeat cycle.
* Every claim is routed to Simone via `route_all_to_simone` (`services/agent_router.py:50-55`, `services/dispatch_service.py:46-47, 87, 135`). Every claimed task gets `_routing → simone`.
* Atlas only gets work via `vp_dispatch_mission` (`tools/vp_orchestration.py:191, 339`) — a tool that Simone (or specific code paths like the signal curator at `heartbeat_service.py:2242`) explicitly invokes.
* `_available_agents_for_llm_routing` (`services/todo_dispatch_service.py:191-198`) enforces the cap (`UA_MAX_CONCURRENT_VP_GENERAL=2` by default) but is consulted by the LLM ROUTER (Simone), not by Atlas itself. It does NOT cause Atlas to autonomously pull work.

**Bottom line:** Atlas sits idle when Simone is busy even if there's work for Atlas and Atlas slots are free. The throttle is architectural (Simone-must-route), not capacity (Atlas-cap).

Partial existing infrastructure to leverage: `services/proactive_convergence.py:556, 562, 640, 646` already tags tasks at creation with `metadata.dispatch.preferred_vp = "vp.general.primary"`. So there's a "pre-routed for Atlas" lane sitting in metadata that a direct-dispatch sweep could consume without waiting for Simone's judgment.

## 14.2 — Cody concurrency clarification (session-level vs internal)

Two distinct layers, both important:

| Layer | Semantics | Enforcement |
|---|---|---|
| Task Hub session-level cap | One outer Cody session at a time = `UA_MAX_CONCURRENT_VP_CODER=1` | Task Hub `_available_agents_for_llm_routing` |
| Inside one Cody session (Anthropic mode) | Near-unlimited: parallel tool calls, parallel sub-agents via Task tool, parallel file ops, agent teams patterns | Anthropic SDK + the model itself |

The session-level cap is correct — we want one outer Cody session to avoid stomping/budget thrash. **The inside-the-session parallelism MUST stay model-driven.** When wiring the Cody Anthropic toggle:

* Do NOT pass concurrency-restricting flags to the SDK
* Do NOT cap Task tool spawns
* Do NOT serialize tool calls
* Add a dashboard note: when a Cody session is in Anthropic mode, "active_coder=1" can mean 1 session with 5 internal sub-agents and 20 parallel ops. Operators should interpret accordingly.

## 14.3 — New requirement: Simone awareness of agent-direct dispatches

Kevin raised this as a closing concern: when Atlas (or later, any direct-dispatched agent) picks up tasks via the Atlas-direct-dispatch lane (Rank 1, §14.4), Simone loses the "I know what's happening" context that lets her intervene if something is needed. Today this works because Simone is the dispatcher — she sees every claim. With Atlas-direct-dispatch, she could be in the middle of a long heartbeat tick while Atlas independently claims and starts work, and Simone wouldn't know until her next briefing context refresh.

**The fix is observability, not gating.** Specifically:

1. **Emit a `delegation_fyi` event** in `task_events` (or the equivalent UA notifications table) whenever the Atlas-direct-dispatch lane claims a task. Payload: `{"agent_id": "vp.general.primary", "task_id": ..., "claimed_at": ..., "preferred_vp_source": "...", "lane": "atlas_direct"}`.
2. **Surface these events in Simone's heartbeat briefing** so she sees "Atlas is working on X (started 3 min ago)" as part of her standard situational awareness. She doesn't need to act — just have the context. If she wants to intervene, she still has `redirect_to` / `request_revision` (Rank 0 verbs).
3. **Same shape for any future direct-dispatch lanes** (e.g., if we ever let proactive Atlas tasks claim without preferred_vp pre-tagging). The pattern is "agent claims autonomously → emit delegation_fyi → Simone briefing surfaces it."

This preserves Simone's substitute-Kevin role (she knows the state of the system, can intervene if needed) without making her a serial bottleneck for delegation.

## 14.4 — Final near-term plan (Hermes Adaptation Plan)

Implemented as phased plan in `docs/reports/hermes-adaptation-phased-plan-2026-05-10.md`. Summary table:

| Rank | Item | Phase | Why ranked here |
|---|---|---|---|
| 0 | `rehydrate` action + Simone-callable `re_evaluate` / `redirect_to` / `request_revision` verbs | B | Prerequisite for autonomous feedback loop; without these, every adaptation slots into a hard human gate. |
| 1 | Atlas-direct-dispatch lane + delegation_fyi events + briefing surface | C | Today's architectural throttle; queue-throughput leverage. Includes §14.3 awareness requirement. |
| 2 | Cody Anthropic-vs-ZAI toggle (preserve internal concurrency) | E | Capability boost for harder coding tasks; internal parallelism must NOT be throttled. |
| 3 | Per-task `max_retries` override | A | Small additive column; quick win. |
| 4 | Wire `release_stale_assignments` into `dispatch_sweep` | A | Existing function, not currently called per-tick; small wiring fix. |
| 5 | `task_hub_runs` attempt-history table | D | Feeds the failure-context input that Rank 0 verbs need to be useful. |
| 6 | Owned-worker Hermes-style reliability (Cody / ToDo dispatch / cron) | F | Real subprocess-level resilience for workers we DO own. |

**Demoted from original §5:**
- Protocol-violation gate (was orig #3) — folded into Phase F (`_classify_worker_exit`).

**Future / deferred (not in this plan):**
- §13.5 capability-aware tiered judge (Tier 1 Simone → Tier 2 Cody-Anthropic → Tier 3 human)
- Atlas-as-Tier-1.5-judge for second-opinion routing
- Raising `UA_MAX_CONCURRENT_VP_GENERAL` from 2 once Atlas utilization is measured

## 14.5 — Open verifications still to do

* Atlas runtime model assignment confirmation (assumed ZAI per CLAUDE.md § Claude Execution Environments; verify in service config).
* Cody Anthropic launcher routing: which entry point + env state activates Anthropic mode for Cody specifically (vs. interactive Kevin sessions which already use it).
* Once Phase C ships: measure how many tasks actually flow through the Atlas-direct lane vs Simone-routed, and decide if `UA_MAX_CONCURRENT_VP_GENERAL` should be raised.

## 14.6 — Decisions captured for the implementation plan (default choices)

These are sensible defaults I'm carrying into the phased plan doc. Confirm any you disagree with. (v2 marks initial corrections from deeper code reads; v3 marks fixes to v2's own errors found on third verification pass.)

| Decision | Default chosen | Reason |
|---|---|---|
| Cody Anthropic toggle surface | Both: env var (system-wide default) + per-task field on `task_hub_items` (per-task override) | Same pattern as per-task `max_retries`; matches the way most existing UA config works |
| Atlas direct-dispatch eligibility | Conservative — only tasks with `metadata.preferred_vp == "vp.general.primary"` (top-level path; **v3 fix**) are dispatched via the Atlas-direct lane. Untagged tasks still go through Simone-routing. | Avoids surprise Atlas dispatches; widen later if utilization data supports it |
| Atlas direct-dispatch failure fallback | The Atlas-direct sweep calls `dispatch_vp_mission` which uses an idempotency key. If the VP runtime fails the mission, existing VP retry / failure semantics apply. If we want to re-route to Simone after Atlas failure, that's a Phase B `redirect_to` decision Simone makes after observing the failure in her briefing. | Don't build complex automatic fallback; let Simone judge |
| `rehydrate` action caller | Operator-only via dashboard initially; Simone-callable via tool added in Phase D once `task_runs` enriches her context | Don't give Simone the verb until she has the failure context to judge from |
| `redirect_to` / `request_revision` callers | Simone only (orchestrator-tier) | Keep the verb scoped narrowly; revisit if Atlas-as-judge becomes a thing |
| Awareness mechanism (**v2 correction**) | `metadata.dispatch.atlas_direct_*` tracking fields on the task itself + briefing-context query for recent direct-dispatched missions | UA has no `task_events`-style table; `task_hub_notifications` is dedup-keyed (no payload). Task metadata is the right home — atomic with dispatch, already surfaced in dashboard and briefing |
| `task_hub_runs` rollout | Additive — new table, all writes optional initially, dashboard view in same phase. **This IS the Hermes `task_runs` duplication identified in §5 #2.** | Don't gate Phase B on Phase D; let the verbs work with whatever context exists today, enrich when D lands |
| Phase E scope (**v3 fix overrides v2**) | Toggle is FULLY in-repo. Applies across 4 Cody execution paths: VP SDK in-process, VP CLI subprocess, autonomous-mission worktree, demo workspace. No external VP runtime coordination required. | v3 correction: VP runtime lives at `vp/worker_loop.py` IN this repo with three execution modes (sdk/cli/dag). v2 wrongly claimed it was external. |
| Phase F scope (**v3 fix overrides v2**) | Observability + per-task timeout configurability across THREE owned-subprocess sites: cron `!script`, VP CLI client (`claude_cli_client.py:191`), demo workspace (`cody_implementation.py:312`). All three already have SIGTERM/SIGKILL at the spawn site; F adds PID tracking + exit classification + per-task timeout + protocol-violation detection. | v3 correction: v2 narrowed F to cron only based on the wrong belief that VP CLI was external. VP CLI is in-repo and already has timeout+kill — F unifies observability across sites. |


