# Durability Testing Runbook

This runbook explains how to execute the durability suite, what each test does, and how to interpret results.

## Why This Exists
Durability tests validate that the agent can recover after crashes without duplicating side effects. The core risk is double-sends (email, uploads) or broken replay (missing outputs).

## Quick Start
1) Export env:
   - `export PYTHONPATH=src`
   - `export UA_TEST_EMAIL_TO=<email>`
2) Run each test from the matrix (see below).
3) Resume each crash using the printed resume command or:
   - `PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>`

## Tests and Purpose

### Test 1: Task crash (RELAUNCH)
**Purpose:** ensure sub-agent work relaunches cleanly without TaskOutput polling.
**What it validates:**
- Task relaunch input normalization.
- Sub-agent output reuse if artifact exists.
**Expected:**
- Original Task marked `abandoned_on_resume`.
- Relaunched Task completes or output reused.

### Test 2: PDF render crash (Bash)
**Purpose:** ensure side-effecting Bash work replays safely.
**What it validates:**
- Bash replay uses idempotency and ledger tracking.
**Expected:**
- PDF exists once.
- Upload/email only once.

### Test 3: Upload crash (upload_to_composio)
**Purpose:** ensure upload receipt dedupes and prevents extra email sends.
**What it validates:**
- `upload_to_composio` receipt persistence.
**Expected:**
- Upload replayed once.
- Email sent once.

### Test 4: Post-email / pre-ledger crash (COMPOSIO_MULTI_EXECUTE_TOOL)
**Purpose:** validate pending receipt promotion on resume.
**What it validates:**
- No duplicate email if crash occurs after provider success.
**Expected:**
- Pending receipt promoted.
- Email not re-sent.

### Test 5: Read-only replay
**Purpose:** verify read-only tool calls replay safely.
**What it validates:**
- Replay correctness for read-only tools.
**Expected:**
- list_directory replayed.
- read_local_file succeeds.
- No side effects.

### Test 6: Replay drain continuation
**Purpose:** ensure replay mode exits cleanly and does not block subsequent tool calls.
**What it validates:**
- Replay queue drains and normal tool execution resumes in the same run.
**Expected:**
- No "Forced replay completed" responses in transcript/run.log.
- Upload/email execute once after replay completes.

## Full Commands

### Test 1 (Task crash)
```
UA_TEST_CRASH_AFTER_TOOL=Task \
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

### Test 2 (Bash crash)
```
UA_TEST_CRASH_AFTER_TOOL=Bash \
UA_TEST_EMAIL_TO=<email> \
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json
UA_TEST_EMAIL_TO=<email> \
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

### Test 3 (Upload crash)
```
UA_TEST_CRASH_AFTER_TOOL=upload_to_composio \
UA_TEST_EMAIL_TO=<email> \
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json
UA_TEST_EMAIL_TO=<email> \
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

### Test 4 (Post-email pre-ledger crash)
```
UA_TEST_CRASH_AFTER_TOOL=mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL \
UA_TEST_CRASH_STAGE=after_tool_success_before_ledger_commit \
UA_TEST_EMAIL_TO=<email> \
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json
UA_TEST_EMAIL_TO=<email> \
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

### Test 5 (Read-only replay)
```
UA_TEST_CRASH_AFTER_TOOL=mcp__local_toolkit__list_directory \
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/read_only_resume_job.json
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

### Test 6 (Replay drain continuation)
```
UA_TEST_CRASH_AFTER_TOOL=Task \
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json
UA_TEST_EMAIL_TO=<email> \
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

### Replay drain check
```
rg -n "Forced replay completed" /home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_<STAMP>/transcript.md
rg -n "Forced replay completed" /home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_<STAMP>/run.log
```

## Related Sub-agent Evaluation (Latest)
These are not crash/replay tests, but they validate sub-agent behavior and
Letta capture after durability changes.

- **Sub-agent capture + history**:
  `uv run python tests/test_letta_subagent.py`
- **Sub-agent isolation**:
  `uv run python tests/test_letta_subagent_isolation.py`
- **Report run confirmation**:
  Trigger a report Task and confirm Letta lists
  `universal_agent report-creation-expert` with its own memory blocks/messages.

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

## Troubleshooting
- **Tool permission stream closed** during crash: expected when the crash hook exits mid-call. Resume and proceed.
- **Email prompt during resume**: ensure `UA_TEST_EMAIL_TO` is set for tests 2–4.
- **TaskOutput attempts**: should not appear. If they do, check pretool guardrails and disallowed tools.
