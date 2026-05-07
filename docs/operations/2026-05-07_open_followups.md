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

## Followup #3 — `vp_mission` Kanban mirror bypasses agent-capability gate

> **Title history:** Previously titled *"Simone executed a code-author `vp_mission`"* — that was the symptom. The verified root cause (this section) is mirror-and-reopen behavior in `tools/vp_orchestration.py` + `task_hub.py`, not a Simone-side bug.

**Priority:** P1. This is the failure that produced the rogue branch in the first place. Recurrence is likely without the fix — the original mission was tier-2 production-coder work that aged out of the VP coder queue, got mirror-reopened as a generic `vp_mission` row, and Simone's dispatch sweep claimed it on the very next tick.

### Symptom

`vp-mission-df2c39bb1e41c6f63d972894`, with title *"Proactive code quality cleanup: add or improve missing docstrings on public functions and classes in the Universal Agent repository at /opt/universal_agent"*, was claimed by `daemon_simone_todo` and executed by Simone (verified via `pgrep -af 'claude_agent_sdk'` finding PID 1725931 with Simone's full system prompt embedded in its cmdline).

Simone is a heartbeat orchestrator. Code-mutation work belongs to `vp.coder.primary`, or to the autonomous-mission worktree contract documented in [`docs/deployment/ai_coder_instructions.md`](../deployment/ai_coder_instructions.md) (tier-2: worktree → patch → syntax check → unit tests → push to `<bot>/<task-id>` branch → PR → CI passes → human merges). This mission did none of that. It executed in-place against `/opt/universal_agent` and crashed CSI cron with a SyntaxError.

### Root cause (verified by code reading)

`tools/vp_orchestration.py` mirrors **every dispatched VP mission** as a claimable row in Task Hub with `source_kind='vp_mission'` and `agent_ready=True`. When the VP worker doesn't claim in time, `reopen_stale_delegations` flips the mirror row to `OPEN`, and `daemon_simone_todo`'s dispatch sweep claims it because `task_hub.py:claim_next_dispatch_tasks` has **no `source_kind` or target-agent filter**. `route_all_to_simone` then tags the claim with `agent_id=simone`, and Simone's runtime executes the mission body under her identity prompt.

In other words: the Kanban mirror was meant as visibility for VP-owned work, but because (a) it inherits `agent_ready=True`, (b) the reopen path resets it to `OPEN` when stale, and (c) the dispatcher has no source_kind filter, it functions as a *fallback claim queue* for any agent — including agents who have no business executing the mission body.

For the specific incident: the original delegation was likely a tier-2 docstring-cleanup mission targeting `vp.coder.primary`. That worker either was down, was busy on a longer mission, or hit its hold timeout. The reopen ran, Simone's next dispatch sweep saw an `OPEN` `agent_ready=True` row that looked claimable, and pulled it in.

### Code paths cited

| Stage | File / Lines | Behavior |
|---|---|---|
| Producer (creates the original delegation) | `services/proactive_codie.py:71-139` | Builds the docstring-cleanup mission for `vp.coder.primary`. |
| Kanban mirror (the gap) | `tools/vp_orchestration.py:285-303` | Mirrors VP mission to Task Hub as `source_kind='vp_mission'`, `agent_ready=True`. |
| Reopen on staleness | `task_hub.py:reopen_stale_delegations:4331-4376` | Flips the mirror row from claimed/in_progress back to OPEN when the VP worker holds without progress. |
| Claim filter (the missing filter) | `task_hub.py:claim_next_dispatch_tasks:1550-1682` | No `source_kind` filter, no target-agent filter. Any caller's claim sweep can claim any `OPEN` `agent_ready=True` row regardless of source_kind. |
| Simone tag | `services/agent_router.py:48-55` | `route_all_to_simone` writes `agent_id=simone` onto whatever was claimed by `daemon_simone_todo`. |
| Heartbeat directive (contributory) | `memory/HEARTBEAT.md:9` | Tells Simone to drive Task Hub work generally; doesn't exclude `vp_mission`. |

### Surprise finding — tier-2 contract not yet on main

The autonomous-mission worktree contract referenced as the prevention for this exact failure mode (`autonomous_mission_executor.py`, `worktree_utils.py`) is on `feature/latest2` **but NOT on `main` as of `f4c793e2`**. It landed in commit `566ddb27` / PR #153, currently sitting on `feature/latest2`.

This means the production Simone after the recovery (running off `main` at `f4c793e2`) is operating *without* the worktree contract that was supposed to make this whole class of failure impossible. If a stale-delegation reopen hits her dispatch sweep again before PR #153 reaches main, the same incident can recur.

We should also check: was PR #153 the work the docstring-cleanup mission was *trying to deliver*? Or is PR #153 already-shipped scaffolding for a contract that was supposed to *enforce* on this kind of work? The session transcript treated the contract as already-deployed — that mental model was wrong, and that's worth flagging in the postmortem under "what we thought was deployed."

### Recommended fix (defense in depth — ship together on `feature/latest2`)

**Option B (primary, ~10 LoC) — fix at the producer side.**

`tools/vp_orchestration.py`'s Kanban mirror writes `agent_ready=False` (or skips writing entirely) for `vp_mission` rows. VP missions stay tracked in Task Hub for visibility/auditing but are *not claimable* through the normal dispatch sweep. VP workers reach in and claim them by `task_id` directly (the path they already use), not via dispatch.

```python
# tools/vp_orchestration.py:285-303 (sketch)
task_hub.upsert_item(
    conn,
    {
        "task_id": vp_mission_task_id,
        "source_kind": "vp_mission",
        # ...existing fields...
        "agent_ready": False,            # ← was True; flip to False
        # OR equivalently: don't insert this row through upsert_item at all,
        # and write to a separate vp_mission_log table for visibility.
    },
)
```

**Option A (backstop, ~30 LoC) — fix at the dispatcher side.**

Plumb a `forbidden_source_kinds` filter through `dispatch_service.dispatch_sweep` → `todo_dispatch_service` → `task_hub.claim_next_dispatch_tasks`. For Simone's claim sweep, set `forbidden_source_kinds={"vp_mission"}`.

```python
# task_hub.py:claim_next_dispatch_tasks (sketch)
def claim_next_dispatch_tasks(
    conn,
    *,
    limit=1,
    agent_id="heartbeat",
    forbidden_source_kinds: set[str] | None = None,   # ← new
    ...
):
    where = ["status = ?", "agent_ready = 1"]
    params = [TASK_STATUS_OPEN]
    if forbidden_source_kinds:
        placeholders = ",".join("?" * len(forbidden_source_kinds))
        where.append(f"source_kind NOT IN ({placeholders})")
        params.extend(forbidden_source_kinds)
    # ... rest unchanged
```

Why both: Option B closes the producer-side hole. Option A is a backstop in case some other path ever introduces a similar pattern. They're independent and small.

### Tests

Add `tests/unit/test_dispatch_source_kind_isolation.py`:

1. **vp_mission rows are not claimable by a non-VP claimer.** Create a `vp_mission` row via `tools/vp_orchestration` mirror. Run a Simone-flavored dispatch sweep. Assert the `vp_mission` row is not in the returned claims.
2. **Simone's claim sweep skips `source_kind=vp_mission` rows even when they're OPEN + agent_ready=True.** (Belt-and-suspenders for Option A.)
3. **Reopen path doesn't transmute the row.** After `reopen_stale_delegations` runs, the `agent_ready` field on a `vp_mission` row is still `False`. (Catches the case where reopen sets `agent_ready=True` as a side effect.)

### Effort

1.5–2 hours total: ~10 LoC for Option B, ~30 LoC for Option A, plus 3 unit tests. Half-day with PR cycle through `pr-validate.yml`.

### Open ops questions (record but not blocking)

- **Was `vp.coder.primary` actually down/unhealthy** at the time the original delegation aged out and got mirror-reopened? If so, that's a separate signal worth investigating (worker health → restart loop).
- **Is PR #153 (the tier-2 worktree contract) on track to merge to main, or was it abandoned?** If it merges, the Kanban-mirror hole still exists but the worktree contract sandboxes the damage. If it's abandoned, the Kanban-mirror fix is the only defense.

### Containment until fixed

Until this lands, **do not enqueue any `vp_mission` against `/opt/universal_agent` directly.** All code-mutation work for the production tree must go through the autonomous-mission worktree contract: separate `<bot>/<task-id>` branch, separate worktree, PR through `pr-validate.yml`, human merge. No Simone-driven in-place edits. If a vp_mission slips through anyway, recovery is the sequence in this incident's postmortem.

---

## Tracking

These follow-ups are durable. They will live in this doc until shipped. When each is shipped, update the entry with the commit SHA + date. Don't delete entries — incident-archeology depends on them.

| # | Status | Shipped | Notes |
|---|---|---|---|
| 1 — Reconciler `TERMINAL_STATUSES` protection | open | — | Workaround in place (use `parked`). Fix is a small `task_hub.py` change. |
| 2 — Deploy.yml local `main` sync | open | — | Cosmetic-only impact. One-line workflow change. |
| 3 — `vp_mission` Kanban mirror bypasses agent-capability gate | **open / verified root cause** | — | Track B diagnosis confirmed: `tools/vp_orchestration.py:285-303` mirrors VP missions as `agent_ready=True`, `task_hub.py:reopen_stale_delegations:4331-4376` reopens them, `task_hub.py:claim_next_dispatch_tasks:1550-1682` has no `source_kind` filter, Simone's sweep claims them. Fix is small (Option B: ~10 LoC at the mirror). |
