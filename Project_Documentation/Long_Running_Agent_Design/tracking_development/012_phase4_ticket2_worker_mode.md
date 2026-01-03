# 012: Phase 4 Ticket 2 — Worker Mode (Lease + Heartbeat)

Date: 2026-01-02  
Scope: Background run execution with DB-backed leasing and heartbeat

## Summary
Implemented a worker entrypoint that polls the runtime DB, acquires leases on eligible runs, executes them via `--resume`, and maintains heartbeats until completion. The lease model prevents multiple workers from processing the same run concurrently.

## What Changed
### Worker entrypoint
- `src/universal_agent/worker.py`
  - Polls for runs in `queued` or `running` state with expired/no lease
  - Acquires lease and runs `python -m universal_agent.main --resume --run-id <id>`
  - Sends periodic heartbeats while the run is active
  - Releases lease when the run finishes

### Runtime DB fields (runs table)
- `lease_owner`
- `lease_expires_at`
- `last_heartbeat_at`

### DB helpers
Added to `src/universal_agent/durable/state.py`:
- `list_runs_with_status`
- `acquire_run_lease`
- `heartbeat_run_lease`
- `release_run_lease`

## Worker Usage
Poll mode (continuous):
```
PYTHONPATH=src uv run python -m universal_agent.worker --poll
```

Single-run mode:
```
PYTHONPATH=src uv run python -m universal_agent.worker --once
```

## Environment knobs
- `UA_WORKER_POLL_SEC` (default: 5)
- `UA_WORKER_HEARTBEAT_SEC` (default: 10)
- `UA_WORKER_LEASE_TTL_SEC` (default: 30)

## Lease Semantics
1) Worker queries runs with status `queued` or `running`.
2) Lease is acquired if `lease_expires_at` is null or expired.
3) Heartbeat refreshes `lease_expires_at` while the run is executing.
4) Lease is released on process exit.

## Notes
- Worker uses the same resume path as CLI, so existing durability behavior remains intact.
- The operator CLI (`runs show`) now surfaces lease fields for debugging.
