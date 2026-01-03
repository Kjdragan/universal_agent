# Ticket 1 — Failure Injection Hooks (Next Steps)

Date: 2026-01-02

## Summary
Implemented crash hook matching for normalized tool names with optional step/phase gating, and added focused unit tests. Updated the long-run test doc with new env vars.

## Why
The test-only crash hook must be deterministic, flexible, and safe-by-default to validate worst-case crashes without changing runtime behavior when unset.

## Changes
- Added normalized tool name matching and optional `UA_TEST_CRASH_AFTER_PHASE` / `UA_TEST_CRASH_AFTER_STEP` filters.
- Preserved existing crash stages (`after_tool_success_before_ledger_commit`, `after_ledger_mark_succeeded`).
- Added unit tests for matching behavior.
- Documented new env vars in long-run test instructions.

## Files
- `src/universal_agent/main.py`
- `tests/test_crash_hooks.py`
- `Project_Documentation/Long_Running_Agent_Design/how to run long run test.md`

## Repro Command
```
UA_TEST_CRASH_AFTER_TOOL=gmail_send_email \
UA_TEST_CRASH_STAGE=after_tool_success_before_ledger_commit \
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json
```

## Pass/Fail Signal
- **Pass**: Process exits with code 137 and logs `UA_TEST_CRASH_AFTER_TOOL triggered`, resume completes without duplicate side effects.
- **Fail**: No crash occurs with env var set, or duplicate side effects occur on resume.

## Regression Check
```
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/quick_resume_job.json
```
Kill during sleep, then resume:
```
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```
Expect clean resume and completion.

## Tests Run
```
UV_CACHE_DIR=/home/kjdragan/lrepos/universal_agent/.uv_cache uv run pytest tests/test_crash_hooks.py
```

## Notes
- Crash hook behavior is no-op unless env vars are set.
- Normalization allows `GMAIL_SEND_EMAIL` and `gmail_send_email` to match.
