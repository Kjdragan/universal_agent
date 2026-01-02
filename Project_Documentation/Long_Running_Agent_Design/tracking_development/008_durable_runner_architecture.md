# 008: Durable Runner Architecture (Current State)

Date: 2026-01-02
Scope: Durable job execution, resume, provider session continuity, and observability

## Overview
The durable runner uses a runtime SQLite DB, tool-call ledger, and workspace artifacts to make long-running jobs resumable after interrupts. Job runs persist state and tool receipts so a `--resume` can reconstruct a minimal resume packet, auto-continue without prompting, and avoid duplicate side effects. Provider session IDs (when available) are stored to optionally resume server-side conversation state in addition to local rehydration.

## Main Components
1) CLI entrypoint
   - `python -m universal_agent.main` handles `--job`, `--resume`, and `--fork`.
   - Creates run_id + workspace, initializes Claude SDK + Composio tools.

2) Runtime DB (SQLite)
   - Location: `AGENT_RUN_WORKSPACES/runtime_state.db`
   - Primary tables:
     - `runs`: run metadata (status, run_mode, job_path, last_job_prompt, provider_session_id, parent_run_id).
     - `run_steps`: per-iteration steps with status.
     - `tool_calls`: ledger of tool invocations with idempotency keys.
     - `checkpoints`: serialized snapshots (including interrupt checkpoints).

3) Tool Call Ledger
   - Tracks tool calls with idempotency keys and side-effect classification.
   - Used to detect and avoid duplicating successful side effects after resume.

4) Checkpointing
   - Saved at step boundaries and on SIGINT (interrupt).
   - Last checkpoint is loaded on resume and used to build the resume packet.

5) Resume Packet
   - Summary of run_id, checkpoint_id, step info, recent + in-flight tool calls, artifacts.
   - Persisted to workspace as `resume_packet_<run_id>.md` and injected into the resume prompt.

6) Job Completion Summary
   - Written to `job_completion_<run_id>.md` with status, artifacts, and last side-effect receipts.

7) Provider Session Continuity (optional)
   - On `ResultMessage.session_id`, store `provider_session_id`.
   - On resume (if present), set `continue_conversation=True` and `resume=<provider_session_id>`.
   - Forks (`--fork`) use `fork_session=True`, and persist `parent_run_id` + `provider_session_forked_from`.

8) Workspaces + Artifacts
   - Each run uses `AGENT_RUN_WORKSPACES/session_<timestamp>`.
   - Standard outputs: `run.log`, `trace.json`, `transcript.md`, resume/job summary files, work_products/.

9) Observability (Logfire)
   - Spans record checkpoints, ledger events, tool calls, and step transitions.
   - Useful for correlating multiple trace IDs per run (initial + resume + checkpoint).

## Execution Flow (Job Run)
1) Start `--job`:
   - Parse run spec, create run_id + workspace.
   - Persist run metadata in `runs`.
2) Agent loop:
   - Classify query, execute tool loop, write tool calls to ledger, checkpoint at boundaries.
3) Completion:
   - Update run status to succeeded/failed.
   - Write job completion summary and update restart file.

## Execution Flow (Resume)
1) Load run + last checkpoint.
2) Build resume packet from DB + workspace.
3) If job mode and status not terminal/waiting:
   - Auto-continue with injected resume message + last_job_prompt.
4) On completion:
   - Write job completion summary and update restart file.

## Known Limitations (Observed)
1) In-flight tool calls are not resumed
   - When a tool is in "running" state at interrupt, the resumed run proceeds with new tool calls.
   - In the latest test, the sleep command was not re-run; a diagnostic command was run instead.

2) Provider session ID capture can be delayed
   - If the run is interrupted before a ResultMessage, provider_session_id is unavailable for resume.

3) Interrupt handling can create multiple checkpoints
   - Rapid/double SIGINT can emit multiple interrupt checkpoints.

## Current Test Harness (quick_resume_job.json)
- Step 1: `sleep 30` (clear interrupt point)
- Step 2: write `work_products/resume_test.txt` with timestamp
- Step 3: `ls -la`
- Step 4: reply DONE

This test validates auto-resume, resume packet creation, and side-effect dedupe but still surfaces the in-flight tool reconciliation gap.

## Key Files
- Flow + CLI: `src/universal_agent/main.py`
- DB schema/migrations: `src/universal_agent/durable/migrations.py`
- DB state updates: `src/universal_agent/durable/state.py`
- Runtime DB path: `src/universal_agent/durable/db.py`
- Test job spec: `tmp/quick_resume_job.json`

