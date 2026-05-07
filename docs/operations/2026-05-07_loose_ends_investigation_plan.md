# 2026-05-07 — Loose-Ends Investigation Plan

**Audience:** Operator + future incident-response readers.
**Purpose:** Verify that *all other* deliverables from the 2026-05-07 session got handled properly, separately from the CSI intelligence pass redesign. The recovery from the rogue Codie/Simone branch produced multiple follow-up items; this doc tracks each one through its current status, what verification is needed, and what action (if any) remains.

**Why this exists:** The CSI v2 backfill turned out to have a fundamental design problem (regex-based extraction). That doesn't mean the *other* fixes from this session are also broken — most are separate concerns. But the operator's reasonable concern is: how do we know the other items are actually shipped and not just documented? This doc answers that.

**Scope:** Everything that came up in the 2026-05-07 working session that's NOT the CSI intelligence pass redesign. The redesign has its own plan at [`../proactive_signals/csi_intelligence_pass_implementation_plan_2026-05-07.md`](../proactive_signals/csi_intelligence_pass_implementation_plan_2026-05-07.md).

---

## Inventory of items to investigate

| # | Item | Source | Expected status |
|---|---|---|---|
| 1 | Followup #1 — Reconciler ignores `TERMINAL_STATUSES` | `2026-05-07_open_followups.md` | OPEN — workaround in place, not coded |
| 2 | Followup #2 — `deploy.yml` doesn't sync local `main` | `2026-05-07_open_followups.md` | OPEN — not coded |
| 3 | Followup #3 — `vp_mission` Kanban mirror bypasses agent gate | `2026-05-07_open_followups.md` (root cause verified by Track B) | OPEN — root cause confirmed, fix not coded |
| 4 | `IsADirectoryError` bug in `claude_code_intel_replay.py:281` | Found during v2 backfill | OPEN — not coded (will be folded into CSI redesign PR per Phase F) |
| 5 | PR #153 (tier-2 worktree contract) — merge to main | Postmortem Dead End 5 | UNKNOWN — on `feature/latest2`, status of merge to main not verified |
| 6 | Original handoff Item 1 — CSI v2 Phase 2/3 smoke verification | `2026-05-07_handoff_followups.md` | DONE in this session via manual Ops API fire |
| 7 | Original handoff Item 4 — VPS `gh` CLI cleanup | `2026-05-07_handoff_followups.md` | LIKELY DONE — needs confirmation |
| 8 | Original handoff Item 5 — GitHub branch protection | `2026-05-07_handoff_followups.md` | UNKNOWN — operator UI work |
| 9 | Recovery loose end — Codie capture branch lifecycle | This session's recovery | OPEN — preserved on origin, never merged or PR'd |
| 10 | Recovery loose end — `vp-mission-df2c39bb` parked state | This session's recovery | RESOLVED — parked, agent_ready=0, won't re-fire |

---

## Verification approach (per item)

For each item, the investigation pattern is:

1. **Read-only state check** — grep, git log, sqlite query, file inspection
2. **Categorize** — DONE / OPEN / NEEDS-OPERATOR-ACTION / NEEDS-CODE
3. **Action decision** — what should happen next, who does it, whether it goes through tier-2 PR contract or operator-direct

The investigation is doc-only and read-only at first. Code or PR work follows in dedicated PRs.

---

## Item-by-item plan

### Item 1 — Followup #1: Reconciler ignores TERMINAL_STATUSES

**Verification commands:**
```bash
# Has any commit since the postmortem touched task_hub.py around the upsert guard?
cd /home/ua/dev/universal_agent
git log --oneline --since='2026-05-07 18:30' -- src/universal_agent/task_hub.py
# Read the specific lines that need the guard
sed -n '1000,1015p' src/universal_agent/task_hub.py
# Look for any new symmetric guard for TERMINAL_STATUSES → IN_PROGRESS
grep -n 'TERMINAL_STATUSES.*IN_PROGRESS\|TERMINAL_STATUSES.*OPEN' src/universal_agent/task_hub.py
```

**Expected finding:** No commits, no symmetric guard. Status is OPEN exactly as the followups doc says.

**Action plan if confirmed OPEN:**
- Small PR (~10 LoC + 50 LoC tests). Could be a tier-2 PR opened independently from the CSI redesign.
- Branch: `claude/reconciler-terminal-status-guard`
- Tests: `tests/unit/test_task_hub_terminal_protection.py`

**Action plan if surprise FOUND:** Just confirm the implementation matches the followup doc's design and that tests cover it.

---

### Item 2 — Followup #2: deploy.yml doesn't sync local main

**Verification commands:**
```bash
# Has the deploy workflow been touched?
git log --oneline --since='2026-05-07' -- .github/workflows/deploy.yml
# Read the workflow's git operations
grep -nE 'git fetch|git reset|git checkout|git pull|git update-ref' .github/workflows/deploy.yml
```

**Expected finding:** No new commit, the workflow still does `git fetch && git reset --hard origin/main` without advancing local `main`.

**Action plan if confirmed OPEN:**
- Single-line workflow change. Probably tier-2 PR.
- Branch: `claude/deploy-yml-sync-local-main`
- Verification: after merge, check `/opt/universal_agent` on the VPS has `main == origin/main` after a deploy.

---

### Item 3 — Followup #3: vp_mission Kanban mirror

**Verification commands:**
```bash
# Producer side: did anyone change tools/vp_orchestration.py mirror logic?
git log --oneline --since='2026-05-07' -- src/universal_agent/tools/vp_orchestration.py
# Read the specific lines (per Followup #3 — lines 285-303)
sed -n '280,310p' src/universal_agent/tools/vp_orchestration.py
# Consumer side: does claim_next_dispatch_tasks now have a source_kind filter?
grep -n 'source_kind\|forbidden_source_kinds' src/universal_agent/task_hub.py | head -10
```

**Expected finding:** Both still as-is. Status OPEN.

**Action plan if confirmed OPEN:**
- This is the **highest priority** of the three follow-ups. Recurrence likely without the fix.
- Per Followup #3 doc: Option B (~10 LoC at the producer) + Option A (~30 LoC source_kind filter at dispatcher) + 3 unit tests = ~1.5-2 hours.
- Branch: `claude/vp-mission-kanban-gate-fix`
- Tests: `tests/unit/test_dispatch_source_kind_isolation.py` per the spec in Followup #3

**Note:** This MUST land before any future autonomous-mission work resumes, or the rogue-branch incident can recur.

---

### Item 4 — IsADirectoryError bug in claude_code_intel_replay.py

**Verification commands:**
```bash
sed -n '275,285p' src/universal_agent/services/claude_code_intel_replay.py
git log --oneline --since='2026-05-07' -- src/universal_agent/services/claude_code_intel_replay.py
```

**Expected finding:** Still uses `.exists()` instead of `.is_file()` at line 281.

**Action plan:**
- Per the CSI implementation plan (Phase F), this fix is bundled into the CSI redesign PR.
- No separate PR needed.
- Verify after merge: re-run backfill `--dry-run`, the 2026-04-20 ClaudeDevs packet should not error.

---

### Item 5 — PR #153 lifecycle

**Verification commands:**
```bash
# What's the state of PR 153 on github?
gh pr view 153 --json state,mergedAt,baseRefName,headRefName,title 2>&1 || echo "(gh not configured or PR doesn't exist)"
# Independent check: are autonomous_mission_executor.py and worktree_utils.py on main?
git ls-tree -r origin/main -- src/universal_agent/vp/autonomous_mission_executor.py src/universal_agent/vp/worktree_utils.py
git ls-tree -r origin/feature/latest2 -- src/universal_agent/vp/autonomous_mission_executor.py src/universal_agent/vp/worktree_utils.py
# Last commit on main referencing the contract files
git log origin/main --oneline -- src/universal_agent/vp/autonomous_mission_executor.py src/universal_agent/vp/worktree_utils.py | head -5
```

**Expected finding:** Files exist on `feature/latest2` but NOT on `main`. PR #153 status: probably still open.

**Action plan:**
- If PR #153 is still open and ready to merge: ask operator to drive a `/ship` (which routes feature/latest2 → develop → main).
- If PR #153 is abandoned: open a new PR with the same content.
- If unclear: surface to operator.

**This is operator-action**, not code-action. The plan's deliverable is a clear status report + recommendation, not a code change.

---

### Item 6 — Original handoff Item 1 (CSI v2 smoke verification)

**Status:** DONE in this session.

**Evidence:**
- `cron_result_30.md` from manual fire at 19:01-19:08 UTC: `Status: success`, no traceback, `action_count: 0`
- `cron_result_31.md` from scheduled 16:00 CDT fire: produced packet at `2026-05-07/210014__bcherny`
- vault still receiving updates (per `vault_manifest.json.updated_at`)

**Action:** Mark closed in this doc. No further work.

---

### Item 7 — Original handoff Item 4 (VPS gh CLI cleanup)

**Verification (already done at start of this plan-execution):**
```bash
which gh                  # /usr/bin/gh
ls -l /home/ua/.local/bin/gh  # No such file
gh auth status            # Logged in as Kjdragan with repo scope
```

**Status:** DONE — silently resolved at some point. The shadow binary at `/home/ua/.local/bin/gh` no longer exists; `which gh` correctly resolves to `/usr/bin/gh`. Auth is healthy.

**Action:** Mark closed.

---

### Item 8 — Original handoff Item 5 (GitHub branch protection)

**Verification commands:**
```bash
gh api 'repos/Kjdragan/universal_agent/branches/main/protection' 2>&1 | head -30
gh api 'repos/Kjdragan/universal_agent/branches/develop/protection' 2>&1 | head -30
gh api 'repos/Kjdragan/universal_agent/branches/feature%2Flatest2/protection' 2>&1 | head -30
```

**Expected finding:** Likely 404 / "Branch not protected" responses on at least one of them.

**Action plan:**
- If unprotected: this is operator UI work. Surface a recommendation to apply the rules per the original handoff doc § Item 5.
- If protected: confirm the rules match what was specified and mark closed.

**This is operator-action.** The plan's deliverable is a status report.

---

### Item 9 — Codie capture branch lifecycle

**Status:** Branch `origin/codie/docstring-cleanup-task-hub @ 57c6d4e6` exists with 6 docstring-only commits (Simone's work captured during recovery).

**Verification commands:**
```bash
gh pr list --head codie/docstring-cleanup-task-hub --state all 2>&1
git log --oneline origin/main..origin/codie/docstring-cleanup-task-hub
```

**Action options:**
- (a) Open a real PR for the docstring work. It's docstring-only, low-risk, but it's also not urgent. Could go through normal review.
- (b) Abandon it. Delete the branch. Lose the docstring work permanently.
- (c) Leave as-is. Branch sits on origin indefinitely as a parked snapshot.

**Recommendation:** (c) for now. The branch is preserved per the postmortem; deciding whether to merge it can wait. The author identity (`VP Analysis Agent` and `Simone (captured pre-reset)`) is not what we'd want on docstring commits going forward — better to re-do the docstrings under proper agent identity later.

**Action:** Document the decision (c) in this doc. No further work.

---

### Item 10 — vp-mission-df2c39bb parked state

**Verification commands:**
```bash
sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db \
  "SELECT task_id, status, stale_state, seizure_state, agent_ready, updated_at
     FROM task_hub_items
    WHERE task_id = 'vp-mission-df2c39bb1e41c6f63d972894';"
```

**Expected finding:** Still `parked / parked_manual / unseized / agent_ready=0` since the recovery park SQL.

**Action:** Confirm and mark closed. The mission is permanently parked; it won't re-fire. If the operator wants the docstring work redone properly later, that's a new mission, not a re-claim of this one.

---

## Execution order

1. **Run all read-only verifications** (Items 1, 2, 3, 4, 5, 6, 7, 8, 10) in parallel where possible — single Bash invocation. Should take ~1-2 min total.
2. **Update this doc** with findings appended as a `## Investigation results — <date>` section.
3. **Decide actions:**
   - Items confirmed DONE → mark closed
   - Items needing operator UI work (Items 5, 8) → surface recommendations to operator
   - Items needing code (1, 2, 3, 4) → decide whether to bundle into existing PR (Item 4 → CSI PR) or open new tier-2 PRs
   - Item 9 → document the decision
4. **Open the dedicated tier-2 PRs** for code items 1, 2, 3 (each independent, can ship in any order):
   - `claude/reconciler-terminal-status-guard` (Item 1)
   - `claude/deploy-yml-sync-local-main` (Item 2)
   - `claude/vp-mission-kanban-gate-fix` (Item 3) — **highest priority**

## Priority ordering (when work happens)

| Priority | Item | Why |
|---|---|---|
| P1 | Item 3 — vp_mission Kanban mirror | Recurrence risk for the rogue-branch incident |
| P1 | Item 4 — IsADirectoryError | Bundled into CSI redesign PR; no extra cost |
| P2 | Item 1 — Reconciler TERMINAL_STATUSES | Workaround exists (use `parked`); fix is small |
| P2 | Item 2 — deploy.yml local main sync | Cosmetic; no production impact |
| P2 | Item 5 — PR #153 to main | Required before next autonomous-mission work; not blocking ops today |
| P3 | Item 8 — Branch protection | Defense in depth; not blocking ops |
| P3 | Item 9 — Codie capture branch | Decide later |
| Closed | Items 6, 7, 10 | Already done |

---

## Companion to the CSI redesign

This investigation plan is **independent** of the CSI intelligence pass redesign. The CSI redesign is on its own track (its plan: [`../proactive_signals/csi_intelligence_pass_implementation_plan_2026-05-07.md`](../proactive_signals/csi_intelligence_pass_implementation_plan_2026-05-07.md)) and ships under its own PR (`claude/csi-intelligence-pass-mvp`).

The only overlap: Item 4 (`IsADirectoryError`) is a CSI-replay bug found during the failed v2 backfill, and the CSI redesign PR will fix it as a free incidental — that's documented in the CSI plan's Phase F.
