# How to Run Long-Run Test

This document describes the simplest, repeatable durability test flow.

## Start the Run
Run the CLI with a job JSON file. This prints a Run ID and auto-runs the job prompt.

```
PYTHONPATH=src uv run python -m universal_agent.main --job durable_demo.json
```

The resume command is also saved to:

`Project_Documentation/Long_Running_Agent_Design/KevinRestartWithThis.md`

## Kill the Run
To stop the run at any time, press:

```
Ctrl+C
```

## Resume the Same Run
Use the Run ID from `KevinRestartWithThis.md` to resume exactly where it left off.

```
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

## Notes
- Use the same `durable_demo.json` for a consistent test scenario.
- The resume command always requires the original Run ID.
- The job file should include either `prompt` or `objective`. If only `objective` is present, it is used as the prompt, with inputs/constraints appended.

## Ticket Features in This Flow (with Ticket Numbers)
- Phase 0–3: Runtime DB + tool ledger (runs/steps/tool_calls/checkpoints)
- Phase 0–3: Replay policies: REPLAY_EXACT / REPLAY_IDEMPOTENT / RELAUNCH
- Phase 0–3: Task relaunch on resume (TaskOutput/TaskResult guardrail)
- Phase 0–3: In-flight tool replay + recovery-only tool enforcement
- Phase 0–3: Run-wide completion summary (aggregated across resumes)
- Phase 0–3: Provider session continuity with fallback
- Phase 0–3: Crash hooks for tool-boundary failure injection
- Phase 0–3: Config-driven tool policies (`durable/tool_policies.yaml`)
- Phase 4 — Ticket 1: Operator CLI (`ua runs list/show/tail/cancel`)
- Phase 4 — Ticket 2: Worker mode (lease/heartbeat background runner)
- Phase 4 — Ticket 3: Policy audit (`ua policy audit`)
- Phase 4 — Ticket 4: Receipts export (`ua runs receipts`)
- Next Steps — Ticket 7: Durability smoke script (`scripts/durability_smoke.py`)

## Crash Hooks (Test-Only)
Use these env vars to force a hard crash right after a tool succeeds, either
before or after the ledger commit. This is for durability/idempotency testing.

- `UA_TEST_CRASH_AFTER_TOOL=<raw_or_normalized_tool_name>` (case-insensitive)
- `UA_TEST_CRASH_AFTER_TOOL_CALL_ID=<tool_call_id>`
- `UA_TEST_CRASH_AFTER_PHASE=<phase>` (optional)
- `UA_TEST_CRASH_AFTER_STEP=<step_id>` (optional)
- `UA_TEST_CRASH_STAGE=after_tool_success_before_ledger_commit` (default) or
  `after_ledger_mark_succeeded`

Example:
```
UA_TEST_CRASH_AFTER_TOOL=COMPOSIO_MULTI_EXECUTE_TOOL \
UA_TEST_CRASH_STAGE=after_tool_success_before_ledger_commit \
PYTHONPATH=src uv run python -m universal_agent.main --job durable_demo.json
```

The process exits with code 137 and logs the tool_call_id, raw_tool_name, and
crash stage.

## Durability Matrix (Kill Points A/B)
Use `tmp/relaunch_resume_job.json` and the matrix runner to standardize kills.

Kill Point A (subagent running):
1) Start job.
2) Kill when `Task` begins (or set a crash hook on `Task` once it appears).
3) Resume.
Expected: previous Task becomes `abandoned_on_resume`, new Task relaunched.

Kill Point B (email send):
1) Start job with crash hook: `UA_TEST_CRASH_AFTER_TOOL=GMAIL_SEND_EMAIL`.
2) Resume.
Expected: no duplicate email; ledger dedupe/idempotency prevents re-send.

Helper:
```
PYTHONPATH=src uv run python scripts/run_durability_matrix.py --crash-tool GMAIL_SEND_EMAIL --resume-once
```
