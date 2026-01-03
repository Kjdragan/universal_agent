# Durability Test Matrix

This matrix documents repeatable durability kill points and expected outcomes.

## Setup
- Base run: `tmp/relaunch_resume_job.json`
- Resume command is written to: `Project_Documentation/Long_Running_Agent_Design/KevinRestartWithThis.md`
- Runtime DB: `AGENT_RUN_WORKSPACES/runtime_state.db`

## Common DB Checks
```
-- duplicate idempotency keys (should be 0 rows)
SELECT idempotency_key, COUNT(*) AS c
FROM tool_calls
GROUP BY idempotency_key
HAVING c > 1;

-- side-effect tools should not repeat
SELECT tool_name, status, COUNT(*) AS c
FROM tool_calls
WHERE side_effect_class != 'read_only'
GROUP BY tool_name, status;
```

## Notes
- Task replay input normalization ignores `resume` and will map it to `task_key` in PreToolUse, so replay matching stays deterministic.

## Matrix

### 1) Kill during subagent Task (RELAUNCH)
- Start:
  - `PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json`
- Kill when Task starts (or set crash hook after Task succeeds once tool_call_id is known):
  - `UA_TEST_CRASH_AFTER_TOOL=Task PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json`
- Resume:
  - `PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>`
- Expected:
  - Original Task marked `abandoned_on_resume`.
  - Relaunched Task completes (or is skipped if `subagent_output.json` already exists).

### 2) Kill after PDF render but before upload
- Start:
  - `PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json`
- Kill after `Bash` renders PDF (use crash hook with tool_call_id once visible):
  - `UA_TEST_CRASH_AFTER_TOOL=Bash PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json`
- Resume:
  - `PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>`
- Expected:
  - PDF exists once in workspace.
  - Upload/email executed once.

### 3) Kill after upload but before email
- Start:
  - `PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json`
- Kill after upload tool succeeds:
  - `UA_TEST_CRASH_AFTER_TOOL=upload_to_composio PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json`
  - (Canonical) `UA_TEST_CRASH_AFTER_TOOL=mcp__local_toolkit__upload_to_composio ...`
- Resume:
  - `PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>`
- Expected:
  - Upload is not duplicated.
  - Email sent once.

### 4) Kill after email success but before ledger mark (crash hook)
- Start:
  - `UA_TEST_CRASH_AFTER_TOOL=GMAIL_SEND_EMAIL \
     UA_TEST_CRASH_STAGE=after_tool_success_before_ledger_commit \
     PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json`
  - (Canonical raw tool) `UA_TEST_CRASH_AFTER_TOOL=mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL ...`
  - Optional matcher: `UA_TEST_CRASH_MATCH=raw|slug|any` (defaults to `raw`).
- Resume:
  - `PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>`
- Expected:
  - No duplicate email; ledger idempotency prevents re-send.

### 5) Kill during read-only step (replay/idempotent)
- Start job with a read-only tool (e.g., list/read):
  - `PYTHONPATH=src uv run python -m universal_agent.main --job tmp/read_only_resume_job.json`
- Kill during read-only tool call.
- Resume:
  - `PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>`
- Expected:
  - Read-only tool replays safely; no side effects.

## Pass/Fail Summary
- **Pass**: No duplicate side effects; in-flight tools replay deterministically; Task relaunch occurs only when output artifacts are missing.
- **Fail**: Duplicate side effects, missing artifacts, or replays outside forced queue.
