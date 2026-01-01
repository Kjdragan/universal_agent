# Durable Jobs v1 — Technical Design Spec (Universal Agent)

**Purpose:** Upgrade the current “continuous session / task-by-task” agent into a **durable job runner** that can run 30m → multi‑hour reliably, survive restarts, and **avoid duplicating side effects** (email sends, uploads, memory mutations, etc.).

This spec is written to match the repo’s current structure and call chains:
- CLI loop lives in `src/universal_agent/main.py` with `setup_session()`, `process_turn()`, `run_conversation()` consuming `ToolUseBlock` / `ToolResultBlock`, and writing workspace artifacts like `run.log`, `trace.json`, `transcript.md`.  
- Telegram path uses an in-memory `asyncio.Queue` (`TaskManager.worker()`) and a long-lived actor loop (`AgentAdapter._client_actor_loop()`), which we will support after CLI is stable.  
- Local MCP tool server is `src/mcp_server.py` exposing file I/O, crawl pipeline, memory tools, and uploads.  
- Observability is Logfire; tool calls and results are recorded to `trace.json`, and AgentCollege consumes Logfire traces (polling + webhook).  

(These statements are based on the repo component map and call graph you generated.)  

---

## 1) Goals and non-goals

### Goals (v1)
1. **No duplicate external side effects under retries/resume.**
2. **Resume after crash/restart** from the last durable checkpoint with minimal rework.
3. Provide **auditability**: run/step/tool-call receipts stored durably and queryable.
4. Be **CLI-first**, but implement durability in a shared layer so Telegram/WebSocket can adopt it later.

### Non-goals (v1)
- Enterprise security/compliance hardening (YOLO mode).
- Multi-worker horizontal scaling (single-worker is fine in v1).
- Perfect tool metadata classification across the entire Composio catalog (we’ll use robust heuristics + allowlist).

---

## 2) Definitions

### Run
A durable unit of work (one user intent). Identified by `run_id`.

### Step
A bounded unit of execution within a run. Identified by `step_id`. Steps are where we checkpoint and where we enforce budgets.

### Tool Call Ledger
Append-only record of every tool call (inputs, outputs, status, timestamps) with an **idempotency key**.

### Receipt
The stored outcome of a tool call (success/failure + response + correlation IDs). Receipts make resumption deterministic.

### Checkpoint
A durable snapshot of the run’s canonical state at a boundary (typically end of a step/phase). Checkpoints are what allow resuming after restarts.

---

## 3) Architecture (v1)

### 3.1 The most important design choice
**Don’t rely on a “live process” staying alive.** The agent loop may restart. The durable state makes restarts safe.

### 3.2 Component diagram (conceptual)
```
CLI (main.py)
  -> Durable Runner (new: durable/runner.py)
      -> Think Step (Claude Agent SDK: process_turn/run_conversation)
      -> Tool Gateway (new: durable/tool_gateway.py)
          -> Local MCP (mcp_server.py tools)
          -> Composio Tool Router / SDK tools (Gmail, GitHub, etc.)
      -> Persist:
          - Runtime DB (new SQLite): runs, run_steps, tool_calls, checkpoints
          - Workspace artifacts: trace.json, transcript.md, reports
      -> Phase Critic (later phase): AgentCollege
```

### 3.3 Where durability hooks into the existing code
Per your repo mapping:
- Tool calls originate from `ToolUseBlock` events read in `run_conversation()` / `_run_conversation()`; results appear as `ToolResultBlock`, and the repo currently saves them into `trace.json` and triggers observers.  
- We will keep `trace.json` as telemetry, and add a **separate durable ledger** for correctness.  

---

## 4) Persistence boundary

### 4.1 Decision (confirmed)
Use a **separate SQLite DB** for runtime durability (Option 2).

- File: `AGENT_RUN_WORKSPACES/runtime_state.db` (or `Memory_System_Data/runtime_state.db`; pick one, but keep it separate from `agent_core.db`).
- Reason: clean separation between *memory* tables and *durable job execution* tables.

---

## 5) Data model (SQL)

> Minimal tables for v1 (add columns as needed; keep schema small and explicit).

```sql
-- runs: one durable job per user intent
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL,                 -- queued|running|waiting|succeeded|failed|cancelled
  entrypoint TEXT NOT NULL,             -- cli|telegram|ws
  run_spec_json TEXT NOT NULL,          -- immutable contract
  current_step_id TEXT,
  last_checkpoint_id TEXT,
  final_artifact_ref TEXT
);

-- run_steps: bounded steps within a run
CREATE TABLE IF NOT EXISTS run_steps (
  step_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  step_index INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL,                 -- ready|running|blocked|succeeded|failed|skipped
  phase TEXT NOT NULL,                  -- plan|crawl|synthesize|render|upload|send|notify|etc
  error_code TEXT,
  error_detail TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

-- tool_calls: append-only ledger w/ idempotency
CREATE TABLE IF NOT EXISTS tool_calls (
  tool_call_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,

  tool_name TEXT NOT NULL,              -- e.g., GMAIL_SEND_EMAIL or mcp:write_local_file
  tool_namespace TEXT NOT NULL,         -- composio|mcp|local|claude_code
  side_effect_class TEXT NOT NULL,      -- external|memory|local|read_only

  normalized_args_hash TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,

  status TEXT NOT NULL,                 -- prepared|running|succeeded|failed|cancelled
  attempt INTEGER NOT NULL DEFAULT 0,

  request_ref TEXT,                     -- blob ref (file path) or inline JSON
  response_ref TEXT,                    -- blob ref (file path) or inline JSON
  external_correlation_id TEXT,         -- provider request id, message id, etc

  error_code TEXT,
  error_detail TEXT,

  FOREIGN KEY(run_id) REFERENCES runs(run_id),
  FOREIGN KEY(step_id) REFERENCES run_steps(step_id)
);

-- checkpoints: step/phase boundary snapshots
CREATE TABLE IF NOT EXISTS checkpoints (
  checkpoint_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  created_at TEXT NOT NULL,

  checkpoint_type TEXT NOT NULL,        -- step_boundary|phase_boundary|pre_side_effect
  state_snapshot_json TEXT NOT NULL,    -- canonical run state summary
  cursor_json TEXT,                     -- pointers: last tool_call_id, last artifact, etc

  FOREIGN KEY(run_id) REFERENCES runs(run_id),
  FOREIGN KEY(step_id) REFERENCES run_steps(step_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_run_step ON tool_calls(run_id, step_id);
CREATE INDEX IF NOT EXISTS idx_run_steps_run ON run_steps(run_id, step_index);
```

---

## 6) RunSpec (contract)

RunSpec is immutable per run. Keep it small and explicit.

```json
{
  "objective": "Create a research report from N URLs and email it to X",
  "constraints": {
    "must_cite_sources": true,
    "no_duplicate_external_actions": true,
    "allowed_domains": [],
    "forbidden_actions": []
  },
  "budgets": {
    "max_wallclock_minutes": 90,
    "max_steps": 50,
    "max_tool_calls": 250,
    "max_retries_per_tool_call": 5,
    "max_same_error_repeats": 3
  },
  "stop_conditions": [
    "report_pdf_created",
    "email_sent_once",
    "final_notification_sent"
  ],
  "escalation": {
    "ask_human_on_ambiguous_recipient": true,
    "ask_human_on_auth_failure": true,
    "ask_human_on_repeated_failures": true
  },
  "artifacts": {
    "workspace_dir": "AGENT_RUN_WORKSPACES/session_<id>/",
    "expected_outputs": ["report.html", "report.pdf", "transcript.md"]
  }
}
```

---

## 7) Tool side-effect classification (policy)

### 7.1 Why classification is needed
Composio Tool Router exposes a huge, evolving tool universe. We cannot maintain a manual list.

### 7.2 v1 classification rule (conservative)
Default to **side-effect** unless confidently read-only.

- **external**: creates/modifies/sends/deletes on external systems (email, calendar, GitHub, Slack/Telegram, etc.).
- **memory**: mutates persistent memory (core/archival inserts/appends/replaces).
- **local**: writes local files, runs subprocesses, creates archives.
- **read_only**: list/get/search/read operations.

### 7.3 Explicit “known side effects” from your current local MCP server
From `src/mcp_server.py`, treat these as **side effects**:
- External-ish: `workbench_upload`, `upload_to_composio`
- Local: `write_local_file`, `compress_files`, `finalize_research`, `generate_image`, `preview_image`
- Memory: `core_memory_replace`, `core_memory_append`, `archival_memory_insert`

Treat these as read-only:
- `read_local_file`, `read_research_files`, `list_directory`, `archival_memory_search`, `get_core_memory_blocks`, `describe_image`
- Crawls (`crawl_parallel`) are I/O-heavy “reads” that should be checkpointed to avoid rework.

This list aligns with the run log you shared (email send + uploads + local file writes).  

### 7.4 Heuristic for dynamic Composio tools (v1)
- If tool name contains: `SEND|CREATE|UPDATE|DELETE|PATCH|POST|MERGE|UPLOAD|INVITE|PUBLISH|COMMENT|REPLY|FORWARD|ARCHIVE|LABEL|MOVE|MARK|ASSIGN` → side-effect: **external**
- If contains: `GET|LIST|SEARCH|READ|FETCH|RETRIEVE` → **read_only** (unless explicitly overridden)
- Allow override via config:
  - `read_only_allowlist`
  - `side_effect_denylist` (force ledger + idempotency)

---

## 8) Idempotency and tool call lifecycle

### 8.1 When to require idempotency
**Always** for `side_effect_class in {external, memory}`. Strongly recommended for `local` as well.

### 8.2 Idempotency key strategy (v1)
Compute:

```
idempotency_key = sha256(
  run_id +
  tool_namespace +
  tool_name +
  normalized_args_hash +
  side_effect_scope
)
```

Where:
- `normalized_args_hash` is a stable hash of JSON args with keys sorted and irrelevant fields removed.
- `side_effect_scope` is a category-specific “what makes this unique” value (examples):
  - Email send: recipient + subject + attachment hash
  - Upload: local filepath hash + remote destination
  - Memory append: memory kind + scope + content hash

### 8.3 Tool call state machine
1. `PREPARED`: ledger row exists; idempotency key reserved.
2. `RUNNING`: executing tool.
3. `SUCCEEDED`: response stored (receipt).
4. `FAILED`: error stored (receipt).

**Deduping behavior:**
- If a tool call arrives and ledger has **SUCCEEDED** for same idempotency key → return stored response; do not execute.
- If **RUNNING** and stale → reconcile (either query provider by correlation ID, or retry with same idempotency key if provider supports it).
- If **FAILED** → allow retry up to `max_retries_per_tool_call` with backoff (same idempotency key).

---

## 9) Step-boundary checkpointing (v1)

### 9.1 The checkpoint boundary
After each step/phase:
- persist checkpoint snapshot (RunSpec summary + progress markers)
- persist the “cursor” (last tool_call_id, produced artifacts)
- mark step status

### 9.2 Deterministic resume rule
On resume:
- Do **not** re-run tools that already have SUCCEEDED receipts.
- Do **not** re-send external notifications if receipt exists.
- You may re-run read-only crawls only if there is no durable artifact/receipt.

### 9.3 What to checkpoint (minimal)
- current phase + step_index
- list of planned remaining phases (lightweight)
- artifact refs that already exist (paths)
- last tool_call_id

---

## 10) Observability requirements (v1)
- Propagate `run_id`, `step_id`, and `tool_call_id` into Logfire baggage/attributes.
- For local MCP tools (stdio trace separation), include `run_id` / `step_id` as **explicit tool args** where possible, or include them in the request payload for later correlation.

---

## 11) Demo scenario (acceptance test)
**Research → report → render PDF → upload → email** with forced restart:

1. Start CLI run.
2. Force-kill process mid-run (during crawl or before email).
3. Restart with `--run-id <same>` / `--resume`.
4. Verify:
   - report artifacts exist once
   - uploads executed once
   - email send executed once
   - final “done” notification executed once

---

## 12) File/module changes (v1)
Based on your call graph and component map, durability will integrate into these paths:

- `src/universal_agent/main.py` (CLI entrypoint; create run; drive steps)
- `src/universal_agent/agent_core.py` (shared logic for recording tool calls/results; can be wrapped)
- `src/mcp_server.py` (local tools; add optional correlation args; no logic changes required)
- `src/universal_agent/bot/*` (later: adopt durable runner after CLI is stable)
- Add new package: `src/universal_agent/durable/` for DB + ledger + runner + classification.

