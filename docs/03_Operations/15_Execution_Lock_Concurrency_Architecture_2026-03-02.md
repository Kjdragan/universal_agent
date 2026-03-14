# 15 — Execution Lock & Concurrency Architecture

**Date**: 2026-03-02  
**Status**: DECISION REQUIRED — Kevin to review and choose path forward  
**Priority**: High — blocks true parallelism and efficient VPS utilization  

---

## 1. Executive Summary

The Universal Agent gateway has a **global execution lock** (`asyncio.Lock`) that serializes ALL agent session execution. Even with `UA_HOOKS_AGENT_DISPATCH_CONCURRENCY=2`, only one agent session's LLM turns can run at a time. This document explains why the lock exists, what it protects, what concurrency actually means in our system, and the options for moving forward.

**REMINDER**: This is separate from the SQLite DB lock issue (fixed by splitting `activity_state.db` from `runtime_state.db`). This is about agent session execution throughput.

---

## 2. The Problem: What the Execution Lock Does

### 2.1 Where It Lives

```python
# gateway.py line 219-222
# process_turn (and parts of the legacy bridge) rely on global process state
# (stdout/stderr redirection, env vars, module-level globals). Serialize
# all gateway execution to prevent cross-session contamination.
self._execution_lock = asyncio.Lock()
```

### 2.2 What It Wraps

Every call to `gateway.execute()` — the entry point for ALL agent work — acquires this lock:

```python
# gateway.py line 727-730
async def execute(self, session, request) -> AsyncIterator[AgentEvent]:
    async with self._timed_execution_lock("execute"):
        # ... entire agent session runs here
```

The lock is held for the **entire duration** of an agent session — from the first LLM call to the final tool result. A typical hook session (e.g., YouTube tutorial processing) holds this lock for **5-15 minutes**.

### 2.3 Why It Exists: Module-Level Globals in main.py

`main.py` uses **module-level global variables** to track session state:

```python
# main.py lines 184-196
tool_ledger = None
budget_state = {}
trace = {}
run_id = None
runtime_db_conn = None
budget_config = {}
current_execution_session = None
gateway_mode_active = False
current_step_id = None
```

These globals are read and written throughout `process_turn()` and its ~50+ callees. If two sessions ran `process_turn()` concurrently:

- **Session A** sets `run_id = "abc123"`
- **Session B** sets `run_id = "xyz789"` (overwrites A's value)
- **Session A** writes a checkpoint with `run_id` → writes to wrong run
- **Data corruption, cross-session contamination, undefined behavior**

The lock prevents this by ensuring only one session is inside `process_turn()` at any time.

---

## 3. What "Dispatch Concurrency" Actually Means Today

### 3.1 The Dispatch Gate (hooks_service.py)

```python
# hooks_service.py lines 133-140
configured_dispatch_concurrency = max(1, 
    self._safe_int_env("UA_HOOKS_AGENT_DISPATCH_CONCURRENCY", 1))
self._agent_dispatch_concurrency = min(4, configured_dispatch_concurrency)
self._agent_dispatch_gate = asyncio.Semaphore(self._agent_dispatch_concurrency)
```

The dispatch gate controls how many hook sessions can be **in flight** simultaneously. With concurrency=2, two sessions can pass through the gate.

### 3.2 The Two-Lock Bottleneck

```
Hook Event A arrives ──→ Dispatch Gate (semaphore=2) ──→ gateway.execute() ──→ Execution Lock ──→ process_turn()
Hook Event B arrives ──→ Dispatch Gate (semaphore=2) ──→ gateway.execute() ──→ Execution Lock ──→ (BLOCKED)
                                                                                    ↑
                                                                              waits for A
```

With `UA_HOOKS_AGENT_DISPATCH_CONCURRENCY=2`:
- **Both sessions pass the dispatch gate** ✅
- **Session A acquires the execution lock, Session B waits** ❌
- Net result: Session B does get a small head start on workspace setup and YouTube transcript download (which happen before `gateway.execute()`), but all LLM turns are serialized.

### 3.3 What Concurrency=2 Gives You TODAY

| Phase | Concurrent? | Why |
|-------|------------|-----|
| Workspace creation | ✅ Yes | Happens before gateway.execute() |
| YouTube transcript download | ✅ Yes | Happens in `_prepare_local_youtube_ingest()` before execute |
| Session setup (adapter creation) | ✅ Yes | But fast, negligible |
| **LLM turns + tool execution** | ❌ No | Blocked by execution lock |
| **Subagent execution** | ❌ No | Runs inside process_turn(), under the lock |
| **Multi-step pipeline backbone** | ❌ No | Each step is a turn inside process_turn() |

**Bottom line**: Concurrency=2 today gives ~5-10% improvement from overlapping pre-execute work. The 90% of time (LLM turns) is still serial.

---

## 4. Answering Kevin's Key Questions

### 4.1 "Would concurrency mess up the multi-step pipeline backbone?"

**No, because true concurrency doesn't exist yet.** The execution lock prevents it. But this is the RIGHT concern — if we removed the lock without fixing the globals, yes, two sessions running the same pipeline would corrupt each other's state.

### 4.2 "Is concurrency about two different sessions, not within a session?"

**Correct.** The concurrency we're discussing is **inter-session** parallelism:
- Session A: YouTube tutorial pipeline for Video X
- Session B: CSI data analyst webhook processing
- These are completely independent work items that SHOULD be able to run in parallel

**Intra-session** parallelism (within a single pipeline) is a different concept. Within one session, the multi-step backbone is inherently sequential:
1. Ingest → 2. Research → 3. Report → 4. Artifacts → 5. Notify

Each step depends on the previous step's output. This sequential backbone is correct and would NOT change with inter-session concurrency.

### 4.3 "What about background tasks like CSI, heartbeat, cron — are they different?"

**No, they are NOT different architecturally.** Every task type flows through the same path:

```
Telegram DM        ──→ bot → gateway.execute() ──→ execution lock ──→ process_turn()
Heartbeat tick     ──→ heartbeat_service → gateway.execute() ──→ execution lock ──→ process_turn()
CSI webhook        ──→ hooks_service → gateway.execute() ──→ execution lock ──→ process_turn()
YouTube tutorial   ──→ hooks_service → gateway.execute() ──→ execution lock ──→ process_turn()
Cron daily brief   ──→ hooks_service → gateway.execute() ──→ execution lock ──→ process_turn()
```

Every one of these acquires the same execution lock. They are ALL serialized. This means:
- If a YouTube tutorial is running (15 min), a CSI webhook must WAIT 15 min
- If a heartbeat tick fires, it queues behind whatever is running
- If you DM the bot, your request queues behind all pending work

This is the core throughput constraint.

### 4.4 "Would removing the lock break the deterministic pipeline?"

**Not if done correctly.** The fix is to give each session its own state instead of sharing globals. Think of it like this:

| Today (shared globals) | After refactor (per-session state) |
|---|---|
| One kitchen, one chef at a time | Multiple kitchens, one chef per kitchen |
| Chef A leaves ingredients on counter, Chef B picks them up accidentally | Each chef has their own counter, tools, ingredients |
| The execution lock is the "one chef at a time" rule | No lock needed — kitchens are isolated |

Each session's multi-step pipeline backbone would be completely unchanged. The backbone is deterministic WITHIN a session. What changes is that multiple sessions can run their own independent backbones in parallel.

---

## 5. The Three Sessions That Want to Run Concurrently

On our VPS, we typically have these concurrent demands:

1. **Hook sessions** (YouTube tutorials, CSI webhooks, cron jobs) — the primary workload
2. **Heartbeat proactive tasks** — periodic checks, Todoist, system health
3. **Telegram DM requests** — user-initiated queries to Agent007

Today, all three queue behind the execution lock. The user experience impact:
- You DM the bot while a tutorial is processing → you wait 10+ minutes for a response
- A CSI webhook arrives during a heartbeat → it queues for 5+ minutes
- Two YouTube tutorials arrive simultaneously → second one waits for first to complete entirely

---

## 6. Options for Moving Forward

### Option A: Keep Status Quo (No Change)
- **Effort**: Zero
- **Benefit**: Zero — everything stays serialized
- **Risk**: None
- **When**: If throughput is acceptable and the queue doesn't cause timeouts

### Option B: Refactor main.py Globals → Per-Session State (Medium Effort)
- **Effort**: ~2-3 focused sessions. ~100+ references to module globals need threading through a `SessionContext` object.
- **Benefit**: True inter-session parallelism. Multiple sessions run simultaneously.
- **Risk**: Medium — large surface area change, needs thorough testing
- **When**: When throughput bottleneck becomes unacceptable (e.g., tutorials timing out because heartbeat is running)
- **What changes**:
  1. Create `SessionContext` dataclass holding `run_id`, `tool_ledger`, `trace`, `budget_state`, `current_step_id`, `runtime_db_conn`
  2. Thread it through `process_turn()` and all callees
  3. Each `gateway.execute()` creates its own `SessionContext`
  4. Remove or downgrade the execution lock to protect only truly shared resources
- **What stays the same**:
  - The multi-step backbone within each session
  - The deterministic pipeline flow
  - Subagent execution (still sequential within a session)
  - All tool calling and LLM interaction patterns

### Option C: Process-Level Isolation (VP Workers)
- **Effort**: Already partially built (VP worker architecture exists)
- **Benefit**: Complete isolation — each session is a separate OS process
- **Risk**: Higher memory usage (~200MB per worker process)
- **When**: If we want to scale beyond 2-3 concurrent sessions or need hard isolation guarantees
- **What it means**: Each agent session runs in its own `universal-agent-vp-worker@` process, like the existing `vp.coder.primary` and `vp.general.primary` workers

### Option D: Hybrid — Quick Wins Now, Full Refactor Later
- **Effort**: Low for quick wins, deferred for full refactor
- **Benefit**: Meaningful improvement without the risk of the full refactor
- **Quick wins**:
  1. Move heartbeat and Telegram bot to use VP worker processes (they already can connect via ExternalGateway)
  2. Keep hook sessions serialized through the main gateway execution lock
  3. This gives "3 kitchens": main gateway (hooks), heartbeat worker, telegram worker
- **When**: If we want incremental improvement without touching main.py globals

---

## 7. Recommendation

**Option D (Hybrid)** is the pragmatic path:
1. **Immediate**: The activity_state.db separation (already coded) eliminates DB contention — deploy this now
2. **Short-term**: Move heartbeat and Telegram to separate VP worker processes so they don't queue behind hook sessions
3. **Medium-term**: Refactor main.py globals when throughput demands it (Option B)

This gives the user (Kevin) a responsive Telegram bot and reliable heartbeat execution without the risk of the large globals refactor.

---

## 8. Current State Summary

| Component | Status |
|---|---|
| DB contention (CSI vs agent) | ✅ FIXED — `activity_state.db` separation coded, pending deploy |
| `UA_HOOKS_AGENT_DISPATCH_CONCURRENCY` | Set to 2, but execution lock makes it effectively 1 for LLM turns |
| Execution lock | ⚠️ ACTIVE — serializes all gateway.execute() calls |
| Module-level globals in main.py | ⚠️ ROOT CAUSE — prevents removing the execution lock |
| VP worker processes | ✅ EXIST — `vp.coder.primary` and `vp.general.primary` already running |

---

## 9. Decision Points for Kevin

1. **Is the current serialized throughput acceptable for now?** If yes, deploy the DB fix and revisit later.
2. **Is Telegram bot responsiveness a priority?** If yes, consider moving the bot to its own VP worker (Option D quick win).
3. **Do we want true multi-session parallelism?** If yes, schedule the main.py globals refactor (Option B) as a dedicated work item.

**Please review and let me know which direction to take. I will remind you about this document if it hasn't been addressed.**
