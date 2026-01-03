# 010: Phase 4 Ticket 1 — Operator CLI + Cancel Flag

Date: 2026-01-02  
Scope: Operator-facing CLI for run inspection + durable cancel request

## Summary
Implemented an operator CLI to list, show, tail, and cancel runs backed by the runtime DB. Added a durable cancel flag and safe-boundary checks in the runner so cancellation stops tool execution cleanly and sets the run status to `cancelled`.

## What Changed
### New operator module
- `src/universal_agent/operator/__init__.py`
- `src/universal_agent/operator/operator_db.py`
- `src/universal_agent/operator/operator_cli.py`
- `src/universal_agent/operator/__main__.py`

### Runtime DB schema additions
- `runs.cancel_requested_at`
- `runs.cancel_reason`

### Runner cancel handling
- PreToolUse denies further tool calls when cancel is requested.
- Safe-boundary checks before replay, before prompt, and before processing a turn.
- Prevents a `cancelled` run from being overwritten as `succeeded` at shutdown.

## Operator CLI Usage
Run with:
```
PYTHONPATH=src uv run python -m universal_agent.operator runs list --limit 10
PYTHONPATH=src uv run python -m universal_agent.operator runs show --run-id <RUN_ID>
PYTHONPATH=src uv run python -m universal_agent.operator runs tail --run-id <RUN_ID> --source both --follow
PYTHONPATH=src uv run python -m universal_agent.operator runs cancel --run-id <RUN_ID> --reason "operator stop"
```

`runs show` includes:
- run metadata (status, job path, workspace)
- provider session info (if any)
- lease fields (lease owner/expiry, last heartbeat)
- cancel fields (requested at / reason)
- last 10 tool calls

## Cancel Semantics
1) Operator sets status to `cancel_requested`.
2) Runner detects this at safe boundaries and marks run `cancelled`.
3) PreToolUse rejects new tool calls once cancellation is requested.

## Testing
Manual operator CLI run:
```
export UV_CACHE_DIR=/home/kjdragan/lrepos/universal_agent/.uv-cache
PYTHONPATH=src uv run python -m universal_agent.operator runs list --limit 3
```

Result: `runs list` returns the latest run and workspace path.

## Notes
- Uses runtime DB at `AGENT_RUN_WORKSPACES/runtime_state.db`.
- Cancellation is durable and safe-boundary only (no mid-tool kill).
