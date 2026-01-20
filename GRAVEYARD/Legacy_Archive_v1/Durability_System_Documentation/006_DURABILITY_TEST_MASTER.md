# Durability Test Master (Code-Verified)

This master test list is derived from current durability logic in:
- `src/universal_agent/main.py`
- `src/universal_agent/durable/`

## Goals
- Prove idempotency (no duplicate side effects).
- Prove deterministic replay for in-flight tool calls.
- Prove Task relaunch without TaskOutput/TaskResult.
- Prove phase checkpoints before side effects.

## Prerequisites
- Repo root: `/home/kjdragan/lrepos/universal_agent`
- Env:
  - `PYTHONPATH=src`
  - `UA_TEST_EMAIL_TO=<email>` for email steps
- Jobs:
  - `tmp/relaunch_resume_job.json`
  - `tmp/read_only_resume_job.json`
- Runtime DB:
  - `AGENT_RUN_WORKSPACES/runtime_state.db`

## Crash Hook Controls
- `UA_TEST_CRASH_AFTER_TOOL=<tool>`
- `UA_TEST_CRASH_AFTER_TOOL_CALL_ID=<id>` (optional)
- `UA_TEST_CRASH_STAGE=after_tool_success_before_receipt|after_tool_success_before_ledger_commit|after_ledger_mark_succeeded`
- `UA_TEST_CRASH_MATCH=raw|slug|any`

## Master Matrix
1) **Task crash (RELAUNCH)**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=Task`
   - Expect: Task replay is deterministic:
     - Reuse `subagent_output.json` if present, or
     - Reuse output files referenced in Task prompt.

2) **PDF render crash (Bash)**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=Bash`
   - Expect: PDF exists once; upload/email once.

3) **Upload crash (upload_to_composio)**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=upload_to_composio`
   - Expect: upload replayed once; email once.

4) **Post-email / pre-ledger crash (COMPOSIO_MULTI_EXECUTE_TOOL)**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL`
     + `UA_TEST_CRASH_STAGE=after_tool_success_before_ledger_commit`
   - Expect: pending receipt promoted; no re-send.

5) **Read-only replay**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=mcp__local_toolkit__list_directory`
   - Expect: read-only replay completes without side effects.

6) **Replay drain continuation**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=Task`
   - Expect: replay drains and normal flow resumes.

7) **Phase checkpoint (pre_side_effect)**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=upload_to_composio`
   - Expect: `pre_side_effect` checkpoint exists for run/step.

## Evidence Requirements
- Evidence is receipt-based only.
- If no receipt exists, the summary must not claim the side effect.

