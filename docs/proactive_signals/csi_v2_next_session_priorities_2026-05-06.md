# CSI v2 — Next Session Priorities (2026-05-06 handoff)

> **Audience:** Whichever Claude Code session picks up CSI v2 work after this one.
> **Status of system at end of session:** Phase 1 fully working in production. Phase 2 producer wired and live, idle waiting for first tier-3 input. Phase 3-5 will fire automatically once Phase 2 produces a workspace.
> **What this doc is:** the unambiguous "do these next, in this order" list, distilled from the 2026-05-06 working session. Read this BEFORE you read anything else in `docs/proactive_signals/`.

---

## 30-second status

| Phase | State |
|---|---|
| 0 — dependency currency | ✅ working |
| 1 — CSI lane (poll → vault → bundle → email) | ✅ working; trust_source bypass shipped 2026-05-06 means linked docs now actually fetch |
| 2 — Simone scaffolds workspace | 🟡 producer wired (`cody_scaffold_request` task type), consumer is Task Hub auto-routing to Simone, **never fired in production** |
| 3 — Cody builds demo | 🔴 gated on Phase 2 firing |
| 4 — Simone evaluates | 🔴 gated on Phase 3 |
| 5 — skill memorialize | 🔴 gated on Phase 4 |

`/opt/ua_demos/` contains only `_smoke` (the PR-7b smoke test workspace). The system is ready to produce its first real demo workspace; it just hasn't seen a qualifying input yet.

---

## DO FIRST (in this order)

### 1. Check whether the scheduled smoke test fired and see what happened

A bash one-shot was scheduled at the end of session 2026-05-06 to fire ~1 hour after `5682fc5` deployed. It triggers a manual CSI run, waits 30 min for Simone's heartbeat, then captures Task Hub + `/opt/ua_demos/` state.

```bash
ssh ua@uaonvps 'cat /tmp/csi_smoke_result.log'
```

**Three possible outcomes you might see in that log:**

- **(a) `cody_scaffold_request` rows present in Task Hub AND a new directory in `/opt/ua_demos/`**: 🎉 the v2 system is end-to-end on production for the first time. Mark v2 done. Pivot to backfill (item 2) and Codex generalization (post-finalization).
- **(b) `cody_scaffold_request` rows present, but `/opt/ua_demos/` still only `_smoke`**: Simone isn't claiming or the skill execution is failing. Run focused investigation — see "If Simone is the bottleneck" below.
- **(c) No `cody_scaffold_request` rows at all**: the manual fire happened but the poll had no tier-3 posts. Wait for an organic tier-3 announcement (likely from "Code with Claude" event posts within 24h) or run the synthetic positive test (item 1b below).

### 1a. Also check organic state since session ended

```bash
ssh ua@uaonvps 'sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/task_hub.db \
  "SELECT task_id, source_kind, status, datetime(created_at, '\''unixepoch'\'') FROM task_hub_items WHERE source_kind IN ('\''cody_scaffold_request'\'', '\''claude_code_demo_task'\'') ORDER BY created_at DESC LIMIT 10;"'
ssh ua@uaonvps 'ls -la /opt/ua_demos/'
```

This catches anything the cron picked up between the smoke test and your return.

### 1b. (Conditional) Force a positive test if no tier-3 input arrived organically

If after 24-48h there's still no `cody_scaffold_request` row, write a 5-line Python script that calls `claude_code_intel.queue_follow_up_tasks` directly with a synthetic tier-3 action. That writes a real row to Task Hub, Simone's heartbeat picks it up exactly as if it were organic. Confirms the consumer side end-to-end without waiting for ClaudeDevs to release a feature.

Caveat: synthetic post will not have a real vault entity, so Simone's `cody-scaffold-builder` skill will fail trying to look up the entity. That tests *failure handling*, not the happy path. Better to wait for organic if you can.

### 2. Run the v2 backfill once Phase 2 is verified

Operator-supervised. Reprocesses historical bcherny/ClaudeDevs packets through the new prompt + trust_source bypass, populating the vault with properly-grounded entries.

```bash
# Dry run first
ssh ua@uaonvps 'cd /opt/universal_agent && uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2 --dry-run'

# Diff-only pass
ssh ua@uaonvps 'cd /opt/universal_agent && uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2 --diff-only'

# Real run (atomic vault swap; reversible via --revert-swap)
ssh ua@uaonvps 'cd /opt/universal_agent && uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2'
```

**Why wait for Phase 2 verification first:** the backfill produces vault entities that flow into Phase 2. If Phase 2 has bugs we don't yet know about, you'd be backfilling incorrect-shape input. Verify Phase 2 first, *then* backfill.

### 3. Address gaps the system showed today (lower priority but real)

#### 3a. Markdown render dashboard route

When you click an artifact link in Mission Control, it serves raw markdown text. Should render GitHub-style.

**Scope:** add `/dashboard/artifact?path=...` route that fetches the raw file from the existing `/api/artifacts/files/...` endpoint and renders it client-side via react-markdown (already in the dashboard's deps). Half a day of work, mostly dashboard React. Doesn't affect any backend logic.

#### 3b. "Build demo for this entity" Mission Control button

Manual escape hatch so the operator can flag a vault entity and force `cody-scaffold-builder` to run, even if Simone hasn't auto-decided to. Useful when Simone's auto-behavior is too cautious for an entity you specifically want demoed.

**Scope:** new backend endpoint `POST /api/v1/csi/entities/<slug>/scaffold` that enqueues a `cody_scaffold_request` directly. New dashboard button on each vault entity card. ~half day.

Don't ship 3b until Phase 2 auto-trigger is verified (item 1). If auto-trigger works, 3b is a nice-to-have, not load-bearing.

---

## DO NOT DO YET (per operator guidance)

The user explicitly deferred these on 2026-05-06:

- **Codex intel lane setup** — not until CSI v2 is genuinely finalized end-to-end. Premature generalization risks reworking patterns that haven't been validated yet.
- **Gemini intel lane** — same reason.
- **Heartbeat-based vault scanning** — would have been the wrong approach (Task Hub already routes); replaced by Phase 2 producer pattern in `5682fc5`.

---

## If Simone is the bottleneck (Phase 2 producing rows but not consuming them)

Verification pattern when scaffold rows exist but `/opt/ua_demos/` stays empty for >2 hours:

```bash
# A. Confirm Simone has a recent heartbeat session
ssh ua@uaonvps 'ls -lt /opt/universal_agent/AGENT_RUN_WORKSPACES/ | grep -i simone | head -5'

# B. Check her recent heartbeat invocations for cody-scaffold-builder traces
ssh ua@uaonvps 'grep -rE "cody-scaffold-builder|cody_scaffold_request" /opt/universal_agent/AGENT_RUN_WORKSPACES/cron_simone_heartbeat*/ 2>/dev/null | tail -20'

# C. Check Task Hub for stuck in_progress claims
ssh ua@uaonvps 'sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/task_hub.db \
  "SELECT task_id, status, datetime(updated_at,'\''unixepoch'\'') FROM task_hub_items WHERE source_kind = '\''cody_scaffold_request'\'' AND status IN ('\''in_progress'\'',  '\''claimed'\'') ORDER BY updated_at DESC;"'
```

Common fixes:
- If C shows tasks stuck in_progress > 2h: enable `UA_TASK_STALE_ENABLED=1` to let Task Hub's stale-task policy reset them.
- If B shows no heartbeat traces of the skill: Simone's heartbeat might be skipping CSI work due to other directives. Check her HEARTBEAT.md priority order.
- If A shows no recent heartbeat sessions: heartbeat itself is broken, separate issue.

---

## Open policy decisions (no rush, but worth deciding)

1. **Tier-1 post storage.** Currently every tier-1 post creates a `sources/<post-id>.md` lightweight wiki page. ~500B each. At 10 posts/day → 1.8 MB/year. Probably fine; revisit if vault search becomes noisy.

2. **`research_allowlist` strategy.** Today the allowlist gates only `research_grounding` (Phase 1 open-web search), NOT the linked-source fetcher (which now uses `trust_source=True` and skips the allowlist entirely for official-handle URLs). Could eventually unify by removing the allowlist and trusting any URL that came from a curated handle. Defer.

3. **Tier classifier calibration.** "Code with Claude" conference schedule tweet was tier 1, which is technically correct (it's an event announcement, not a feature drop). When the event posts roll in announcing specific features, those should be tier 3. If they're not, the classifier prompt needs tuning. Worth reviewing the next 5-10 ClaudeDevs posts after the event.

---

## Reminders / institutional knowledge

- The other AI coder is the `/ship` operator. They know the `feature/latest2 → develop → main` flow. After committing, you don't run `/ship` yourself; you wait for them.
- This Claude Code session runs in a **sandboxed VM** without SSH access to the VPS. Diagnostic commands must be relayed through the user (run from their `kjdragan@mint-desktop` terminal). For ops-heavy work, consider running Claude Code directly on the user's desktop or on the VPS — see `CLAUDE.md` "Production Verification Rules" §7 (sandbox honesty).
- `CLAUDE.md` got two new rule sections in this session: **Pre-Implementation Reading** (read existing infrastructure before proposing new logic) and **Production Verification Rules** (skill-deployed ≠ skill-invoked, etc.). Both are non-negotiable. Read them before proposing changes.

---

## What I'm satisfied with at end of session

- Phase 1 is genuinely working with real evidence (6m23s run time = trust_source fetching docs aggressively, emails landing).
- Phase 2 producer is structurally correct and minimal (one task-type change, no orchestration logic in HEARTBEAT.md, leverages Task Hub's existing dispatch_sweep + route_all_to_simone).
- The CLAUDE.md rules are designed to prevent the same class of "shipped 17 PRs and tests passed but nothing actually runs" failure that triggered this session.

## What I'm explicitly NOT satisfied with

- We have not yet seen one full Phase 2/3 chain run on production. By the system's own verification rule #2 ("Phase complete = real artifact on real disk"), v2 is not done until that happens.
- The two QoL fixes (markdown render, demo button) were deferred today and remain deferred. They're polish, not load-bearing, but the user explicitly asked for both.
- The v2 backfill hasn't been run. Historical packets still reflect pre-trust_source quality.

Resolving the unsatisfied items is the work of the next session, in the order listed in "DO FIRST" above.
