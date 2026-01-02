# Long-Running Agents Progress (Universal Agent)

## Intent
We are upgrading the Universal Agent from a short-lived, task-by-task CLI loop into a durable job runner that can resume after restarts and avoid duplicate side effects (email sends, uploads, memory mutations, etc.). The long-running work is organized into phases from the Durable Jobs v1 spec and ticket pack.

## What we implemented so far
### Phase 0 (baseline wiring)
- Added `run_id` and `step_id` propagation through the CLI trace and Logfire spans.
- Added budgets for max wallclock, max steps, and max tool calls with a clean stop.

### Phase 1 (runtime DB + tool call ledger + idempotency)
- Added a new runtime SQLite DB and schema for runs, steps, tool calls, and checkpoints.
- Implemented tool classification (side-effect vs read-only), JSON normalization, and idempotency key generation.
- Wired the Tool Call Ledger into tool execution via Claude SDK hooks:
  - PreToolUse: create ledger row and block duplicate side-effect calls.
  - PostToolUse: store receipts (success/failure + response).
- Added unit tests for idempotency stability, dedupe behavior, and classification.

### Phase 2 (run/step state machine + checkpoints + resume UX)
- Implemented durable run/step state transitions.
- Added step-boundary checkpointing and cursor capture.
- Added CLI resume flags and a printed resume command.
- Resume demo executed: loaded last checkpoint and reused the workspace.
- Added unit tests for state machine and checkpointing.
 - Added SIGINT handling to always save interrupt checkpoints (Ctrl-C reliability).
 - Resume now loads checkpoint but requires manual prompt entry to continue.

### Research pipeline hardening (supporting durable runs)
- `finalize_research` builds a filtered corpus in `search_results_filtered_best/`.
- Filter rules loosened to retain more usable sources.
- `research_overview.md` now explicitly lists filtered-only files and dropped files/reasons.
- Report sub-agent prompt unified to use filtered corpus only.

### MCP Server stabilization
- Fixed indentation/syntax error in `_crawl_core` async helper.
- Removed duplicate import in `src/mcp_server.py`.

## Where this lives
- Durable modules: `src/universal_agent/durable/`
- CLI integration: `src/universal_agent/main.py`
- Tests: `tests/test_durable_*.py`

## Progress log for detailed updates
- `Project_Documentation/Long_Running_Agent_Design/tracking_development/001_phase1_progress.md`
- `Project_Documentation/Long_Running_Agent_Design/tracking_development/002_phase2_progress.md`

## Current status
Phase 2 is implemented. Current focus is validation of kill/resume behavior (checkpoint save/reload + no duplicated side effects) and deciding whether resume should auto-continue or require manual prompt input.
