# Ticket 2 — Prepared-Before-Execute Invariant (Next Steps)

Date: 2026-01-02

## Summary
Enforced prepared-before-execute in the PreToolUse hook, added support for duplicate-safe prepared rows when idempotent tools are allowed to re-run, and added ledger test coverage for duplicate preparation.

## Why
Tool execution must never occur before a prepared ledger row exists. This prevents duplicate side effects after a crash and keeps the runtime DB ledger consistent across resumptions.

## Changes
- Added `allow_duplicate` + `idempotency_nonce` support to ledger preparation for idempotent tool calls that are allowed to run even when a prior receipt exists.
- Updated PreToolUse to deny tool execution when ledger preparation fails.
- Ensured idempotent replays create a prepared row so tool execution is always ledger-backed.
- Enforced prepared-row checks before marking a tool as running.
- Added a unit test to verify duplicate preparation inserts a new row with a unique idempotency key.

## Files
- `src/universal_agent/durable/ledger.py`
- `src/universal_agent/durable/tool_gateway.py`
- `src/universal_agent/main.py`
- `tests/test_durable_ledger.py`

## Repro Command
```
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/quick_resume_job.json
```
Interrupt during `sleep`, resume with:
```
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

## Pass/Fail Signal
- **Pass**: No tool executes unless a prepared ledger row exists; resume completes without duplicate side effects.
- **Fail**: Tool execution proceeds after a ledger prepare failure or missing prepared row.

## Regression Check
```
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json
```
Kill during sleep, resume, and verify the email is sent once.

## Tests Run
```
UV_CACHE_DIR=/home/kjdragan/lrepos/universal_agent/.uv_cache uv run pytest tests/test_durable_ledger.py
```

## Notes
- Duplicate-preparation uses a nonce to keep idempotency keys unique while still recording each execution.
