# Durability Test Runbook (Commands)

This file keeps the exact commands used to run the durability suite.

## Quick Start
1) Export env:
   - `export PYTHONPATH=src`
   - `export UA_TEST_EMAIL_TO=<email>`
2) Run the test with the crash hook.
3) Resume using the run_id printed by the CLI.

## Timeouts
Resume runs can exceed 180s for Task + PDF + sleep + upload + email.
Use **minimum 400s** for Tests 1–4, 6, and 7. Test 5 can use 120s.

## Commands

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

### Test 7 (Phase checkpoint)
```
UA_TEST_CRASH_AFTER_TOOL=upload_to_composio \
UA_TEST_EMAIL_TO=<email> \
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json
```

### Phase checkpoint query
```
SELECT checkpoint_type, step_id, created_at
FROM checkpoints
WHERE run_id = '<RUN_ID>' AND checkpoint_type = 'pre_side_effect'
ORDER BY created_at DESC
LIMIT 3;
```

