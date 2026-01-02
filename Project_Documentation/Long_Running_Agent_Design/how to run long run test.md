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

## Crash Hooks (Test-Only)
Use these env vars to force a hard crash right after a tool succeeds, either
before or after the ledger commit. This is for durability/idempotency testing.

- `UA_TEST_CRASH_AFTER_TOOL=<raw_tool_name>`
- `UA_TEST_CRASH_AFTER_TOOL_CALL_ID=<tool_call_id>`
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
