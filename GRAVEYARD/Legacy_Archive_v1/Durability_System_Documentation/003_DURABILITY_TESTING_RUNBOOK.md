# Durability Testing Runbook (Code-Verified)

This runbook is aligned with the current code behavior in `src/universal_agent/main.py`.

## Quick Start
1) Export env:
   - `export PYTHONPATH=src`
   - `export UA_TEST_EMAIL_TO=<email>`
2) Run each test from the matrix (below).
3) Resume each crash using the printed resume command or:
   - `PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>`

## Timeout Guidance
Resume runs can exceed 180s for Task + PDF + sleep + upload + email.
Use a **minimum 400s** CLI timeout for resume runs in Tests 1–4, 6, and 7.
Test 5 can remain at 120s.

## Test Matrix
1) **Task crash (RELAUNCH)**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=Task`
   - Expect: Task replay via deterministic relaunch or output reuse.

2) **PDF render crash (Bash)**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=Bash`
   - Expect: PDF rendered once; upload/email once.

3) **Upload crash (upload_to_composio)**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=upload_to_composio`
   - Expect: upload replayed once; email once.

4) **Post-email/pre-ledger crash (COMPOSIO_MULTI_EXECUTE_TOOL)**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL`
   - Stage: `UA_TEST_CRASH_STAGE=after_tool_success_before_ledger_commit`
   - Expect: pending receipt promoted; no duplicate email.

5) **Read-only replay**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=mcp__local_toolkit__list_directory`
   - Expect: read-only replay completes.

6) **Replay drain continuation**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=Task`
   - Expect: replay drains and normal flow resumes.

7) **Phase checkpoint (pre_side_effect)**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=upload_to_composio`
   - Expect: `pre_side_effect` checkpoint exists.

## Full Commands
See `Project_Documentation/Long_Running_Agent_Design/020_Durability_Testing_Runbook.md`
for full commands (kept in sync with code).

## DB Checks
```
SELECT idempotency_key, COUNT(*) AS c
FROM tool_calls
GROUP BY idempotency_key
HAVING c > 1;

SELECT tool_name, status, COUNT(*) AS c
FROM tool_calls
WHERE side_effect_class != 'read_only'
GROUP BY tool_name, status;
```

