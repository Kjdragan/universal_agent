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

### Phase 2.5 (job auto-resume + provider session continuity + replay)
- Job-mode resume now auto-continues (no prompt) unless terminal or waiting_for_human.
- Persisted run metadata: run_mode, job_path, last_job_prompt, provider_session_id, parent_run_id.
- Resume packet + job completion summary are written to workspace and linked in `KevinRestartWithThis.md`.
- Provider session continuity: store `provider_session_id` and resume with `continue_conversation=True`.
- Provider session fallback: if resume token invalid/expired, start a fresh provider session.
- Added `--fork` to branch provider sessions into a new run with parent linkage.
- In-flight tool replay: on resume, tools in prepared/running are deterministically re-run before continuation.
- Replay note injected to prevent duplicate tool replays after recovery.
- SIGINT debounced to avoid multiple interrupt checkpoints.

### Phase 3 (replay policy + recovery hardening + audit summaries)
- Replay policy classification added (REPLAY_EXACT, REPLAY_IDEMPOTENT, RELAUNCH).
- RELAUNCH path for Task: on resume, abandon original Task and enqueue a deterministic re-launch.
- TaskOutput/TaskResult guardrail: force RELAUNCH and block direct invocation.
- Recovery-mode guard prevents tool calls after forced replay queue drains.
- Config-driven tool policy map in `durable/tool_policies.yaml`.
- Crash hooks for tool-boundary fault injection (UA_TEST_CRASH_AFTER_TOOL, etc.).
- Run-wide completion summary (aggregated by run_id) printed and persisted to job completion + KevinRestartWithThis.
- Workspace paths resolved to absolute paths in job prompts to avoid $PWD drift.
- Monotonic step_index across recovery/continuation for audit clarity.

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
- `Project_Documentation/Long_Running_Agent_Design/tracking_development/006_provider_session_wiring_report.md`
- `Project_Documentation/Long_Running_Agent_Design/tracking_development/007_resume_continuity_evaluation_quick_job.md`
- `Project_Documentation/Long_Running_Agent_Design/tracking_development/008_durable_runner_architecture.md`

## Current status
Phase 3 durability features are implemented and validated on both quick_resume_job.json and relaunch_resume_job.json. Replay is deterministic, recovery is constrained, side effects are not duplicated, and run-wide summaries are written at completion. Remaining rough edges are mostly noise (headless Chrome DBus warnings) and optional tightening (subagent workspace exploration). Next focus: optional run-wide timing aggregation and any remaining tool-policy tuning as the tool universe grows.
