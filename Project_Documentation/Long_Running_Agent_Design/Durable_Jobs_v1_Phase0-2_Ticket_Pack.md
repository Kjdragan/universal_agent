# Durable Jobs v1 — Phase 0–2 Ticket Pack (Repo-specific)

This is an implementation plan intended for an AI coder with repository access.

Repo facts used:
- CLI loop: `src/universal_agent/main.py` with `setup_session()`, `process_turn()`, `run_conversation()` handling `ToolUseBlock`/`ToolResultBlock`, and writing artifacts like `trace.json`.  
- Telegram path: `TaskManager.worker()` drains an in-memory `asyncio.Queue`, and `AgentAdapter._client_actor_loop()` maintains a live SDK client.  
- Local MCP tools live in `src/mcp_server.py` and include uploads, file writes, memory mutations, and crawl pipeline tools.  
- AgentCollege consumes Logfire traces via polling + webhook.  

---

## Phase 0 — Baseline wiring + guardrails (1 PR)

### P0-1: Add durable run identifiers and propagate through traces
**Scope**
- Introduce `run_id` (UUID) for every CLI invocation.
- Introduce `step_id` per iteration/phase boundary (even if step logic is not durable yet).
- Propagate into Logfire baggage and into `trace.json` entries.

**Files**
- `src/universal_agent/main.py`
- `src/universal_agent/agent_core.py` (where `trace.json` is assembled)

**Acceptance**
- `run_id` appears in Logfire spans and in `trace.json`.
- `run_id` is printed at start (so developers can resume with it).

---

### P0-2: Add budgets / runaway protection config
**Scope**
- Add config for: max wallclock, max steps, max tool calls.
- Hard stop with clean error state.

**Files**
- `src/universal_agent/main.py` (loop control)

**Acceptance**
- A run stops after max steps and writes a final summary artifact.

---

## Phase 1 — Tool Call Ledger + idempotency (foundation)

### P1-1: Create runtime durability DB (SQLite) + migrations
**Scope**
- Add new SQLite DB file (separate from `agent_core.db`).
- Add tables: `runs`, `run_steps`, `tool_calls`, `checkpoints` (from spec).

**Files**
- NEW: `src/universal_agent/durable/db.py` (connect + init schema)
- NEW: `src/universal_agent/durable/migrations.py` (simple “ensure tables exist” is OK in v1)

**Acceptance**
- Running CLI creates DB file and inserts a `runs` row.
- Unit test: schema initializes from scratch.

---

### P1-2: Implement tool classification (side effects)
**Scope**
- Implement `classify_tool(tool_name, namespace, metadata)->side_effect_class`.
- Defaults conservative (side effect unless known read-only).

**Files**
- NEW: `src/universal_agent/durable/classification.py`

**Acceptance**
- Tests classify:
  - `GMAIL_SEND_EMAIL` as external
  - `mcp:upload_to_composio` as external
  - `mcp:core_memory_append` as memory
  - `mcp:write_local_file` as local
  - `COMPOSIO_SEARCH_WEB` as read_only

---

### P1-3: Implement Tool Call Ledger API + idempotency enforcement
**Scope**
- Provide APIs:
  - `prepare_tool_call(...)` → returns existing receipt if deduped, else creates PREPARED row
  - `mark_running(...)`
  - `mark_succeeded(response_ref, correlation_id)`
  - `mark_failed(error)`
- Compute stable idempotency keys from normalized args + run_id + tool identity.
- Store receipts in DB and optionally on disk in workspace.

**Files**
- NEW: `src/universal_agent/durable/ledger.py`
- NEW: `src/universal_agent/durable/normalize.py` (stable JSON normalization + hash)

**Acceptance**
- Unit tests:
  - same tool call args → same idempotency key
  - second execution returns stored receipt without calling executor

---

### P1-4: Wrap tool execution path (where the real value is)
**Scope**
- Intercept tool execution in the CLI path where `ToolUseBlock` is handled (per your repo mapping this is inside `run_conversation()` / `_run_conversation()`).
- When tool is requested:
  1) classify
  2) prepare ledger row
  3) if deduped → inject stored ToolResult back to agent stream
  4) else execute tool via existing gateway (Composio/MCP)
  5) store receipt
- Ensure this works for:
  - Composio tools (e.g., Gmail send)
  - Local MCP tools (uploads, writes, memory mutations)

**Files**
- `src/universal_agent/main.py` (or wherever tool calls are executed)
- `src/universal_agent/agent_core.py` (if tool execution is centralized there)
- NEW: `src/universal_agent/durable/tool_gateway.py` (thin wrapper around existing executors)

**Acceptance**
- Integration test (manual acceptable for v1):
  - run does `upload_to_composio` then `GMAIL_SEND_EMAIL`
  - force-kill after upload
  - resume: upload is not repeated; email sent once

---

## Phase 2 — Run/Step model + step-boundary checkpoints

### P2-1: Implement Run + Step state machine
**Scope**
- Insert `run_steps` rows; mark status transitions.
- Store `current_step_id` on `runs`.

**Files**
- NEW: `src/universal_agent/durable/state.py`
- Update: `src/universal_agent/main.py` loop to execute “one step at a time”

**Acceptance**
- DB shows step progression for a run; failed steps capture error codes.

---

### P2-2: Step-boundary checkpoint snapshots
**Scope**
- At end of each step/phase:
  - write `checkpoints` row with small `state_snapshot_json`
  - include pointers to produced artifacts and last tool_call_id
- On resume:
  - load last checkpoint
  - continue from next step

**Files**
- NEW: `src/universal_agent/durable/checkpointing.py`
- Update: `src/universal_agent/main.py`

**Acceptance**
- Force-kill test:
  - start run
  - kill mid-run
  - resume and complete
  - no duplicate email (ledger proves it)

---

### P2-3: CLI UX for durable runs
**Scope**
- Add flags:
  - `--run-id <uuid>` (create/use)
  - `--resume` (resume existing)
  - `--job <path-to-json>` (optional: run spec file)
- Print a “resume command” at start.

**Files**
- `src/universal_agent/main.py`

**Acceptance**
- A user can reliably resume a run by copying the printed command.

---

## Suggested v1 demo job (acceptance harness)
**“crawl → report → render → upload → email”** (based on your existing workflow).

Minimum proof:
- A run produces report artifacts once.
- Email send is executed once.
- Kill/resume does not duplicate side effects.

---

## Notes for future phases (not in Phase 0–2)
- Phase 3: phase-gated Critic/Repair via AgentCollege
- Phase 4: scheduler/triggers for 24h+ service mode
- Extend durability to Telegram/WebSocket path by swapping entrypoints to use the Durable Runner.

