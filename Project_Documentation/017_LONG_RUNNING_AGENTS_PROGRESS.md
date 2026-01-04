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
- Task replay normalization: `resume` is mapped to `task_key` so replay matching is deterministic.
- Recovery-mode guard prevents tool calls after forced replay queue drains.
- Config-driven tool policy map in `durable/tool_policies.yaml`.
- Crash hooks for tool-boundary fault injection (UA_TEST_CRASH_AFTER_TOOL, etc.).
- Run-wide completion summary (aggregated by run_id) printed and persisted to job completion + KevinRestartWithThis.
- Workspace paths resolved to absolute paths in job prompts to avoid $PWD drift.
- Monotonic step_index across recovery/continuation for audit clarity.

### Phase 4 (operator + worker + receipts + policy audit)
- Operator CLI: `ua runs list/show/tail/cancel` with DB-backed cancel flags.
- Worker mode: lease/heartbeat-based background runner (`python -m universal_agent.worker`).
- Receipts export: `ua runs receipts` (md/json) with external ID extraction.
- Policy audit: `ua policy audit` with unknown-tool detection and input variance report.
- Requirement: add a numbered-prefix project doc for each ticket after completion.

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
- `Project_Documentation/Long_Running_Agent_Design/tracking_development/009_relaunch_resume_evaluation.md`
- `Project_Documentation/Long_Running_Agent_Design/tracking_development/011_relaunch_resume_evaluation_post_fix_v2.md`
- `Project_Documentation/Long_Running_Agent_Design/tracking_development/010_phase4_ticket1_operator_cli.md`
- `Project_Documentation/Long_Running_Agent_Design/tracking_development/012_phase4_ticket2_worker_mode.md`
- `Project_Documentation/Long_Running_Agent_Design/tracking_development/013_phase4_ticket4_receipts.md`
- `Project_Documentation/Long_Running_Agent_Design/tracking_development/014_phase4_ticket3_policy_audit.md`
- `Project_Documentation/Long_Running_Agent_Design/tracking_development/015_durability_smoke_script.md`
- `Project_Documentation/Long_Running_Agent_Design/019_Durability_Testing_Master_Test.md`
- `Project_Documentation/Long_Running_Agent_Design/020_Durability_Testing_Runbook.md`

## Current status
Phase 3 durability features are implemented with deterministic replay, Task relaunch guardrails, and crash-hook driven fault injection. Recovery now forces Complex Path during replay and blocks TaskOutput. Phase 4 includes operator CLI, worker mode, receipts export, and policy audit; remaining Phase 4 work is triggers. The durability suite is fully documented in the master test and runbook, with the matrix in `docs/durability_test_matrix.md` and the read-only job in `tmp/read_only_resume_job.json`. A smoke script exists in `scripts/durability_smoke.py` with documentation in `Project_Documentation/Long_Running_Agent_Design/tracking_development/015_durability_smoke_script.md`. The next planned scope is the remaining tickets in `Project_Documentation/Long_Running_Agent_Design/Durable_Jobs_Next_Steps_Ticket_Pack.md`.

## Durability testing quick-start
- Use the runbook for commands: `Project_Documentation/Long_Running_Agent_Design/020_Durability_Testing_Runbook.md`.
- Use the master test for the canonical matrix and invariants: `Project_Documentation/Long_Running_Agent_Design/019_Durability_Testing_Master_Test.md`.
- Minimum steps:
  - `export PYTHONPATH=src`
  - `export UA_TEST_EMAIL_TO=<email>` for email tests
  - Run the crash tests from the runbook, then resume with the printed command.

## Guardrail updates
- Tool schema guardrails extracted to `src/universal_agent/guardrails/tool_schema.py`.
- Smoke test: `PYTHONPATH=src uv run python scripts/guardrail_schema_smoke.py`.

## Latest durability test results (reference run)
- Run ID: `e7339747-5675-48d5-8248-02bb59561a29`
- Workspace: `/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260102_144640`
- Behavior: interrupted during sleep, resumed cleanly, forced replay re-ran only the sleep, side-effect email sent once after resume
- Run-wide summary: 8 tools total, 0 failed, 1 replayed, 0 abandoned, 3 steps
- Evidence: job completion summary in workspace + Logfire traces linked in evaluation report `009_relaunch_resume_evaluation.md`

## Latest durability suite run (2026-01-04)
- Test 1 Task crash: `56e5bcb9-1b7e-4eb4-abe2-37b7dc47feba`
- Test 2 Bash crash: `3481a025-d3a7-4660-92bc-dcb1cd60ea76`
- Test 3 Upload crash: `86d5a787-5cdb-4045-96dc-f8560cab7de4`
- Test 4 Post-email pre-ledger: `19b48c1c-27b3-444f-b9b4-76f85d021a89`
- Test 5 Read-only: `da317c8f-c299-43ec-acc2-bbf7af8c5755`
- Test 6 Replay drain: `4bacbbd0-bcab-44f0-a8fa-b3151003895a`
- Logs: `/tmp/durability_test1_task_crash.log` ... `/tmp/durability_test6_replay_drain.log`
### Durability testing toolkit (docs + runbook)
- Master test specification: `Project_Documentation/Long_Running_Agent_Design/019_Durability_Testing_Master_Test.md`.
- Runbook with commands/purpose: `Project_Documentation/Long_Running_Agent_Design/020_Durability_Testing_Runbook.md`.
- Matrix: `docs/durability_test_matrix.md` (canonical crash hooks + read-only job).
- Read-only job spec: `tmp/read_only_resume_job.json`.
- Instruction: when you want the matrix executed, request the runbook (run the runbook, not the master test).
