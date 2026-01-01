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

## Where this lives
- Durable modules: `src/universal_agent/durable/`
- CLI integration: `src/universal_agent/main.py`
- Tests: `tests/test_durable_*.py`

## Progress log for detailed updates
- `Project_Documentation/Long_Running_Agent_Design/tracking_development/001_phase1_progress.md`

## Current status
Phase 2 is implemented. Remaining work is validation against the durable job demo scenario (kill/resume with no duplicated side effects) and any follow-on phases.
