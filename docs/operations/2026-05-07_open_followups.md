# 2026-05-07 — Open Follow-ups from Codie Rogue-Branch Recovery

**Audience:** Next AI coder + Kevin.
**Companion:** [`2026-05-07_codie_rogue_branch_recovery.md`](2026-05-07_codie_rogue_branch_recovery.md) (full incident postmortem).
**Status at writing:** Recovery is complete. These three items are durable improvements surfaced by the incident — none of them block normal operations, but each represents a hole that contributed to the rogue-branch event and should be closed before the next time we run an autonomous code-mutation mission.

---

## Followup #1 — Reconciler resurrects `TERMINAL_STATUSES`

**Priority:** P2 (workaround exists — use `parked` instead of `cancelled`).

### Symptom

Operator-issued SQL `UPDATE task_hub_items SET status='cancelled' …` for an `in_progress` task with a `seized` assignment was committed cleanly (1 row changed, transaction COMMIT succeeded). 58 seconds later the row read `status='in_progress'` again, with `metadata_json.dispatch.last_disposition_reason = "reconciled_orphaned_in_progress"`.

The `seizure_state='unseized'`, `agent_ready=0`, `updated_at` (the new ISO timestamp) all survived. Only `status` was clobbered.

### Root cause

`TASK_STATUS_CANCELLED = "cancelled"` is defined in `task_hub.py:22` and listed in `TERMINAL_STATUSES = {COMPLETED, PARKED, CANCELLED}` at line 27. But the upsert / reconcile path has only an asymmetric guard at lines 1004-1009:

```python
# Preserve active non-open states when a source refresh blindly re-upserts as open.
# This avoids clobbering a live claim back to open while the assignment remains seized.
if (
    "status" in item
    and status == TASK_STATUS_OPEN
    and existing_status in {TASK_STATUS_IN_PROGRESS, TASK_STATUS_BLOCKED, TASK_STATUS_REVIEW}
):
    status = existing_status
```

This protects active states from being clobbered to OPEN. There's no symmetric guard protecting terminal states from being clobbered to IN_PROGRESS by an orphan-reconciler.

The orphan reconciler that wrote the `reconciled_orphaned_in_progress` disposition runs periodically inside the gateway (couldn't fully trace which call site in the time available — search `_apply_stale_policy` adjacent code paths and the dispatch_service reconcile loop). It currently treats any `in_progress` row whose assignment has been closed/cancelled as a "stuck task that needs to be re-claimable", and writes through `upsert_item` with the original status. Because the row was cancelled by an operator (terminal status), the reconciler should respect that and leave the row alone.

### Fix sketch

Two-line change in `task_hub.py` around line 1009:

```python
# Existing IN_PROGRESS-protection guard
if (
    "status" in item
    and status == TASK_STATUS_OPEN
    and existing_status in {TASK_STATUS_IN_PROGRESS, TASK_STATUS_BLOCKED, TASK_STATUS_REVIEW}
):
    status = existing_status

# NEW: symmetric terminal-status guard
# Don't let a reconciler / source-refresh clobber an explicit terminal
# disposition. Terminal statuses are deliberate operator/agent decisions.
if (
    "status" in item
    and status in {TASK_STATUS_IN_PROGRESS, TASK_STATUS_OPEN, TASK_STATUS_BLOCKED, TASK_STATUS_REVIEW}
    and existing_status in TERMINAL_STATUSES
):
    status = existing_status
```

### Test

Add `tests/unit/test_task_hub_terminal_protection.py`:

1. Insert a task with `status='cancelled'`. Call `upsert_item` with the same `task_id` but `status='in_progress'`. Assert post-upsert status is still `cancelled`.
2. Same with `status='completed'` — assert preserved.
3. Same with `status='parked'` — assert preserved.
4. Negative test: insert `status='in_progress'`, upsert with `status='cancelled'`. Assert post-upsert is `cancelled` (operator-driven terminal write must be allowed).

### Effort

Small. ~10 lines of `task_hub.py` + ~50 lines of tests. Half-day including PR cycle through `pr-validate.yml`.

### Workaround until fixed

Use `status='parked'` with `stale_state='parked_manual'` for any "halt this task" operation. `parked` survives the reconciler — verified empirically by the `cody_scaffold_request:22f646904a5a3fd8` row which had been parked for 24h+ without any reconciler interference.

---

## Followup #2 — `/opt/universal_agent` local `main` is 97 commits behind `origin/main`

**Priority:** P2 (HEAD is what runs; local branch label is mostly cosmetic — but with a sharp edge).

### Symptom

After P3.4 (`git reset --hard origin/main` on `/opt/universal_agent`), the working tree was correctly at `origin/main` HEAD `f4c793e2`. But the *current branch pointer* was the renamed `codie/docstring-cleanup-task-hub`. When the recovery did `git checkout main` to fix the branch label, local `main` was at `7a76762e` — 97 commits behind `origin/main`. The `git pull --ff-only` that followed updated 139 files, +20,812 lines, including substantial changes to `task_hub.py`, `services/claude_code_intel.py`, and the entire Cody skill family.

That update happened with the gateway already running. There was a 1-2 second window where disk briefly held `7a76762e` files while the gateway had `f4c793e2` in memory. Nothing dynamic-loaded during that window so no observable consequence — but the next time we deploy in this configuration that window could land at a worse moment.

### Root cause

The deploy workflow (`.github/workflows/deploy.yml`) does `git fetch origin && git reset --hard origin/main` on `/opt/universal_agent`. That advances `HEAD` and the working tree but **never advances the local `main` branch pointer**. Over many deploys, local `main` diverges arbitrarily from origin's `main`. Any operator who later does `git checkout main` to verify state will get a stale snapshot.

### Fix sketch

Add one line to `.github/workflows/deploy.yml`:

```yaml
# Existing
- name: Pull latest from main
  run: |
    cd /opt/universal_agent
    git fetch origin --prune
    git reset --hard origin/main

# Add
- name: Sync local main pointer
  run: |
    cd /opt/universal_agent
    git update-ref refs/heads/main "$(git rev-parse origin/main)"
```

Or equivalently restructure to do `git checkout main` then `git pull --ff-only` instead of `reset --hard`. The `update-ref` form is more idempotent and survives a detached HEAD, so prefer it.

### Test

After the deploy.yml change merges and runs once, on the VPS:

```bash
cd /opt/universal_agent
test "$(git rev-parse HEAD)" = "$(git rev-parse main)" \
  && test "$(git rev-parse main)" = "$(git rev-parse origin/main)" \
  && echo "OK"
```

Should print `OK` after every deploy.

### Effort

Small. Single workflow line + one verification step. ~half-hour.

### Why P2 and not P1

`HEAD` is what runs — and `HEAD` was correct after P3.4. The stale `main` label only matters if someone later does `git checkout main` expecting it to be current. That's a "footgun in the recovery toolkit" issue, not an active production bug.

---

## Followup #3 — Simone executed a code-author `vp_mission`

**Priority:** P1. This is the failure that produced the rogue branch in the first place. Could recur tomorrow if not addressed.

### Symptom

`vp-mission-df2c39bb1e41c6f63d972894`, with title *"Proactive code quality cleanup: add or improve missing docstrings on public functions and classes in the Universal Agent repository at /opt/universal_agent"*, was claimed by `daemon_simone_todo` and executed by Simone (verified via `pgrep -af 'claude_agent_sdk'` finding PID 1725931 with Simone's full system prompt embedded in its cmdline).

Simone is a heartbeat orchestrator. Code-mutation work belongs to `vp.coder.primary`, or to the autonomous-mission worktree contract documented in [`docs/deployment/ai_coder_instructions.md`](../deployment/ai_coder_instructions.md) (tier-2: worktree → patch → syntax check → unit tests → push to `<bot>/<task-id>` branch → PR → CI passes → human merges). This mission did none of that. It executed in-place against `/opt/universal_agent` and crashed CSI cron with a SyntaxError.

### Root cause (hypothesis — needs investigation)

Three plausible causes, in order of likelihood:

**(a) Dispatcher doesn't gate `vp_mission` by agent capability.** Inspection of `task_hub.py:claim_next_dispatch_tasks` and `services/dispatch_service.py:dispatch_sweep` does not show an obvious check like "this `source_kind` requires `agent_capability='code_author'` and the claimer's agent_id matches that capability." If true, any agent that pulls from the queue will pick up any `vp_mission` regardless of whether the work suits them.

**(b) Simone's heartbeat directives include a too-broad "claim any vp_mission" rule.** `memory/HEARTBEAT.md` directs her to scan and claim from Task Hub. If the directive doesn't filter by `source_kind` or has a broad fallback ("if nothing else, claim any agent_ready vp_mission"), she will pull code-author missions into her own runtime.

**(c) The mission's `source_kind` routing target was wrong.** The mission was a `vp_mission` source_kind. If there's a routing helper that maps `vp_mission` → preferred_vp, and that mapping defaults to `simone_direct` instead of `vp.coder.primary` for code-mutation missions, the mission pre-routed itself to Simone before being enqueued.

The mission's `metadata_json.preferred_vp` field would tell us which of these applies. From the diagnostic output during recovery: the parked task's metadata showed `preferred_vp: simone_direct` — strongly supporting hypothesis (c) for *this specific mission*. But that doesn't rule out (a) — even with `preferred_vp: simone_direct`, the dispatcher should refuse to give a code-mutation mission to a non-code-mutation agent.

### Investigation plan

Three checks, in order:

1. **Who created `vp-mission-df2c39bb` and what did they pass for `preferred_vp`?**
   ```bash
   sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db \
     "SELECT metadata_json FROM task_hub_items WHERE task_id='vp-mission-df2c39bb1e41c6f63d972894';" \
     | python3 -m json.tool | grep -E 'preferred_vp|source|workflow_kind|created_by|origin'
   ```
   Then: grep the codebase for whoever wrote that origin string. That's the producer.

2. **Does `claim_next_dispatch_tasks` enforce agent-capability matching?**
   Read `task_hub.py:1567-1700`. If there's no `WHERE agent_capability = ?` clause anywhere in the SQL, that's hypothesis (a) confirmed.

3. **Does Simone's HEARTBEAT.md filter by `source_kind`?**
   ```bash
   grep -nE 'source_kind|vp_mission|claim_next' /opt/universal_agent/memory/HEARTBEAT.md
   ```
   If it has a broad "claim any open vp_mission" rule without a code-author exclusion, that's hypothesis (b) confirmed.

The full fix likely requires action on all three:
- Add a `target_agent_kind` column or `metadata.target_agent_kind` field to vp_missions (`code_author | research | analysis | …`).
- `claim_next_dispatch_tasks` filters by it.
- HEARTBEAT.md is updated to never claim `target_agent_kind='code_author'` missions.
- Code-author missions are routed to `vp.coder.primary` by default.

### Companion analysis

A companion analysis of the Simone-vs-VP-coder dispatch routing was started during the operator session but not completed in this transcript. Pick that up before working on the fix.

### Effort

Investigation: 1-2 hours. Fix: probably 1-2 days because it touches the dispatch hot path and likely needs migration of in-flight `vp_mission` rows. Pretty large surface area. Worth scoping carefully before opening a PR.

### Containment until fixed

While this remains unfixed, **do not enqueue any `vp_mission` against `/opt/universal_agent` directly.** All code-mutation work for the production tree must go through the autonomous-mission worktree contract: separate `<bot>/<task-id>` branch, separate worktree, PR through `pr-validate.yml`, human merge. No Simone-driven in-place edits. If a vp_mission slips through anyway, recovery is the sequence in this incident's postmortem.

---

## Tracking

These follow-ups are durable. They will live in this doc until shipped. When each is shipped, update the entry with the commit SHA + date. Don't delete entries — incident-archeology depends on them.

| # | Status | Shipped | Notes |
|---|---|---|---|
| 1 — Reconciler `TERMINAL_STATUSES` protection | open | — | Workaround in place (use `parked`). Fix is a small `task_hub.py` change. |
| 2 — Deploy.yml local `main` sync | open | — | Cosmetic-only impact. One-line workflow change. |
| 3 — Simone vs. vp.coder.primary routing | **open / contains future risk** | — | Most-load-bearing of the three. Investigate before next autonomous code-author mission. |
