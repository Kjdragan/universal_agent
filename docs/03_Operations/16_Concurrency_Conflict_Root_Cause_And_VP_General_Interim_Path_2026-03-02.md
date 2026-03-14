# 16 — Concurrency Conflict: Root Cause, Parallel Execution Model, and VP General Interim Path

**Date**: 2026-03-02  
**Status**: DECISION REQUIRED — choose whether to pursue VP General interim path and/or schedule full refactor  
**Related doc**: `15_Execution_Lock_Concurrency_Architecture_2026-03-02.md`

---

## 1. The Problem in Plain Terms

The Universal Agent can only run **one session at a time**.

While a YouTube tutorial pipeline is running (typically 5–20 minutes), every other piece of work — a Telegram message from you, a heartbeat tick, a CSI webhook, a cron report — waits in a queue. Nothing else moves until the current session finishes and releases the lock. That queue is the bottleneck.

---

## 2. Where the Conflict Arises

### 2.1 All Work Flows Through One Execution Lock

Every unit of agent work, regardless of source, enters through the same gateway and hits the same lock:

```
Your Telegram DM        → gateway.execute() ──┐
Heartbeat tick          → gateway.execute() ──┤ All blocked by
CSI webhook             → gateway.execute() ──┤ _execution_lock
YouTube tutorial hook   → gateway.execute() ──┤ (one at a time)
Cron daily brief        → gateway.execute() ──┘
```

The lock is an `asyncio.Lock` on the `InProcessGateway` singleton. It is not a database lock — it is an in-memory serialization barrier in the gateway process itself.

```python
# gateway.py line 219–222
# process_turn (and parts of the legacy bridge) rely on global process state
# (stdout/stderr redirection, env vars, module-level globals). Serialize
# all gateway execution to prevent cross-session contamination.
self._execution_lock = asyncio.Lock()
```

### 2.2 Why the Lock Has to Exist: Shared Globals in main.py

The lock is not arbitrary. It protects a real hazard. The `process_turn()` function — the heart of every agent session — does not take session state as arguments. Instead, it reads and writes **module-level global variables** in `main.py`:

```python
# main.py lines 184–196
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

These globals are read and written throughout every LLM turn, every tool call, every checkpoint write. There are approximately 50+ internal callees that touch them directly.

### 2.3 What Would Happen Without the Lock

If two sessions ran `process_turn()` simultaneously in the same process:

```
Turn 1 (Tutorial session):   run_id = "tutorial-abc"
Turn 2 (Telegram session):   run_id = "telegram-xyz"   ← overwrites Turn 1's value
Turn 1:   writes checkpoint with run_id → writes to "telegram-xyz" run
Turn 1:   budget_state now belongs to Telegram session
Turn 2:   tool_ledger is half-filled with Tutorial session tool calls
```

The sessions contaminate each other's state. Checkpoints go to wrong runs. Budget tracking fails. Tool call history is corrupted. The multi-step pipeline backbone collapses because each step depends on correct state written by the previous step.

**This is why the lock is not simply removable.** It is the only thing currently preventing data corruption between concurrent sessions.

---

## 3. How True Parallel Execution Would Work

### 3.1 The Fix: Per-Session State, Not Global State

The correct fix is to give each session its **own state context** — essentially, replace those module-level globals with a `SessionContext` object that is created fresh per session and threaded through all callees:

```python
# Current (broken for concurrency):
run_id = "tutorial-abc"          # shared global, gets overwritten
budget_state = {...}              # shared global, gets overwritten

# Fixed (safe for concurrency):
ctx_a = SessionContext(run_id="tutorial-abc",    budget_state={...})
ctx_b = SessionContext(run_id="telegram-xyz",    budget_state={...})
# Each session works in isolation — no overlap possible
```

Once each session has its own `SessionContext`, the execution lock is no longer needed to prevent cross-session contamination. Two sessions can call `process_turn(ctx_a)` and `process_turn(ctx_b)` simultaneously. Their state is isolated by the object they each carry.

### 3.2 What Changes and What Does NOT Change

It is important to understand what "concurrency" means here:

- **What changes**: Two or more independent sessions can run their LLM turns and tool calls in parallel.
- **What does NOT change**: The multi-step pipeline backbone within a single session remains fully sequential and deterministic. Step 2 still waits for Step 1. Step 3 still waits for Step 2. The pipeline structure is an intra-session property. Concurrency is about running *different sessions* simultaneously, not scrambling *steps within a session*.

```
Session A (Tutorial):     Ingest → Research → Report → Artifacts → Notify
Session B (Telegram):     Parse intent → Fetch context → Respond

Both run simultaneously, each following its own sequential backbone.
No step in Session A is affected by anything in Session B.
```

### 3.3 Why This Is a Significant Efficiency Gain

The gateway process spends the majority of every session waiting on:
- Network round-trips to the LLM API (1–30 seconds per turn)
- File I/O during tool execution (seconds per tool call)
- External service calls (YouTube API, RSS feeds, database queries)

Currently, all that waiting is serialized. While Session A waits 10 seconds for a Claude API response, Session B — which may only need 200ms of actual compute — sits blocked in the queue. With per-session state, that idle API-wait time can be used to advance other sessions. The real-world throughput improvement is substantial: likely 3–5x for typical mixed workloads.

---

## 4. Why Fixing This Requires Care

### 4.1 Surface Area

`process_turn()` and its ~50+ callees in `main.py` (4000+ lines) reference these globals directly by name. Every reference must be updated to read from a `SessionContext` argument instead. This is a wide but mechanical refactor — no logic changes needed, just threading state through function signatures.

### 4.2 Regression Risk

This is the core execution path. A missed reference could cause a subtle bug where one session accidentally reads stale state from a previous session. Every modification must be verified with regression tests before deployment.

### 4.3 Scope Estimate

Approximately 2–4 focused development sessions to complete, plus regression testing. Not a large project, but requires careful execution.

---

## 5. The VP General Worker Interim Path — and Why It Helps

### 5.1 What a VP Worker Is

A VP worker (`universal-agent-vp-worker@vp.general.primary.service`) is a **completely separate OS process**. It is not a thread. It is not an async coroutine. It is a separate `systemd` service running its own Python interpreter, its own copy of `main.py`, its own module-level globals, and — critically — its own `InProcessGateway` instance with its own `_execution_lock`.

```
Main gateway process (PID 1984601):
  InProcessGateway._execution_lock  ← Lock A (belongs to this process only)
  run_id, budget_state, trace ...   ← Global state space A

VP General worker process (PID 1986742):
  InProcessGateway._execution_lock  ← Lock B (belongs to this process only)
  run_id, budget_state, trace ...   ← Global state space B
```

Locks A and B are completely independent. They live in different memory spaces. One process acquiring Lock A has absolutely zero effect on Lock B.

### 5.2 Why This Solves the Conflict

When heartbeat and Telegram sessions are routed to `vp.general.primary` instead of the main gateway:

```
Before (everything serialized in main gateway):
  Main gateway lock:  [Tutorial 15min] → [Heartbeat 3min] → [Telegram 30sec] → ...

After (routed to separate worker):
  Main gateway lock:  [Tutorial 15min]         ← runs uninterrupted
  VP General lock:    [Heartbeat 3min]          ← runs simultaneously, different process
  Telegram bot:       [Telegram 30sec]          ← can also target VP General worker
```

The Tutorial, Heartbeat, and Telegram sessions are no longer in the same queue. They are in different processes. Each process runs one session at a time (per its own lock), but **across processes they run in parallel**. The OS schedules them concurrently.

### 5.3 Why This is Not the Complete Solution

The VP General worker still has the same globals problem internally. If two sessions were routed to the same VP General worker simultaneously, they would corrupt each other's state — for the same reason as the main gateway. Each VP worker is currently limited to one concurrent mission (`max_concurrent_missions=1`). So the interim path gives us **multiple single-threaded workers** rather than **one truly concurrent worker**. Think of it as:

- **Interim (VP routing)**: 3 kitchens, each with one chef. Chefs work in parallel. Total parallelism = number of workers.
- **Full refactor**: 1 infinitely scalable kitchen where all chefs can work at the same bench simultaneously.

The VP routing approach is bounded by the number of worker processes. The full refactor removes the bound entirely.

### 5.4 What This Specifically Fixes

By routing heartbeat and Telegram to `vp.general.primary`:

| Scenario | Before | After (VP routing) |
|---|---|---|
| Tutorial running, you DM the bot | Bot waits 15 min | Bot responds immediately (different worker) |
| Tutorial running, heartbeat fires | Heartbeat waits 15 min | Heartbeat runs immediately (different worker) |
| Two tutorials arrive simultaneously | Second waits for first | Still serialized (both would target same gateway) |
| Heartbeat + cron fire simultaneously | One waits for other | Still serialized (both on same VP General worker) |

It is not a complete fix, but it directly addresses the most user-visible pain points: bot responsiveness and heartbeat reliability.

---

## 6. The Full Architecture Picture

```
                          CURRENT STATE
┌─────────────────────────────────────────────┐
│  Main Gateway Process                        │
│  ┌─────────────────────────────────────────┐│
│  │  _execution_lock (ONE lock for ALL)     ││
│  │  ┌────────────────────────────────────┐ ││
│  │  │ Queue: Tutorial → Heartbeat →      │ ││
│  │  │        Telegram → CSI webhook      │ ││
│  │  └────────────────────────────────────┘ ││
│  └─────────────────────────────────────────┘│
└─────────────────────────────────────────────┘

VP workers (vp.general.primary, vp.coder.primary) exist but are only
used for CODIE coding missions today.

                        INTERIM STATE (VP ROUTING)
┌──────────────────────────┐  ┌──────────────────────────────┐
│  Main Gateway Process     │  │  VP General Worker Process    │
│  Lock A                   │  │  Lock B                       │
│  [Tutorial (hook events)] │  │  [Heartbeat sessions]         │
│                           │  │  [Telegram sessions]          │
└──────────────────────────┘  └──────────────────────────────┘
   Runs in parallel with ─────────────────────────────────────^

                       FULL REFACTOR STATE
┌──────────────────────────────────────────────────────────────┐
│  Main Gateway Process (lock removed / downgraded)             │
│                                                               │
│  Session A: Tutorial                runs simultaneously       │
│  Session B: Heartbeat               runs simultaneously       │
│  Session C: Telegram                runs simultaneously       │
│  Session D: CSI webhook             runs simultaneously       │
│                                                               │
│  Each has its own SessionContext — zero shared state          │
└──────────────────────────────────────────────────────────────┘
```

---

## 7. Decision Required

This issue cannot resolve itself. A path must be chosen. The options in priority order:

1. **Do nothing** — Accept serialized throughput. Appropriate only if queue depth stays low and user-facing latency is acceptable.

2. **VP General interim routing** — Route heartbeat and Telegram to `vp.general.primary`. Days of effort. Immediately improves bot responsiveness and heartbeat reliability. Does not solve the fundamental bottleneck for hook/CSI traffic.

3. **Full main.py globals refactor** — Remove the shared-globals constraint and relax the execution lock. The complete and correct fix. Weeks of careful effort. Improves throughput for everything.

4. **Both in sequence** — Start with VP routing now for immediate gains, schedule the full refactor as a deliberate project milestone.

**⚠️ REMINDER: This decision has been deferred since 2026-03-02. Please review and confirm a path forward. See also: `15_Execution_Lock_Concurrency_Architecture_2026-03-02.md` for the full options breakdown.**

---

## 8. Relevant Source Locations

| Concern | File | Lines |
|---|---|---|
| Execution lock declaration | `src/universal_agent/gateway.py` | 219–222 |
| Lock acquired on every execute | `src/universal_agent/gateway.py` | 727–730 |
| Shared globals in main.py | `src/universal_agent/main.py` | 184–196 |
| VP General worker client | `src/universal_agent/vp/clients/claude_generalist_client.py` | 31 |
| VP worker loop (separate process entry) | `src/universal_agent/vp/worker_main.py` | 28–49 |
| VP profile definitions | `src/universal_agent/vp/profiles.py` | 38–46 |
| Dispatch semaphore (hooks) | `src/universal_agent/hooks_service.py` | 133–140 |
