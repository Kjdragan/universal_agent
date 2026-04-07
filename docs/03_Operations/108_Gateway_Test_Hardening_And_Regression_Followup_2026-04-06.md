# 108. Gateway Test Hardening And Regression Follow-Up (2026-04-06)

## Purpose

This document records the gateway/debugging work completed during the April 6-7 regression pass, what was fixed, what remains unfinished, and how to resume the work later without losing context.

This is an operational follow-up document, not a new source-of-truth replacement. Existing canonical docs still govern the underlying subsystems.

## What Was Fixed

### 1. `todo_execution` lifecycle enforcement

The gateway lifecycle repair path was narrowed back to the intended contract:

- unresolved `todo_execution` tasks are no longer auto-completed unconditionally
- server-side auto-complete now happens only after verified final delivery
- runs with no lifecycle mutation and no delivery proof now fail correctly and reopen/review instead of silently completing

Related code/tests:

- `src/universal_agent/gateway_server.py`
- `tests/gateway/test_todo_pipeline_integration.py`

### 2. Repo-backed coding session regression groundwork

The earlier work to support repo-backed coding sessions for Simone/Cody introduced several gateway-adjacent regressions and test drift. During this follow-up, those gateway-side failures were worked down directly rather than being left as “known red”.

### 3. Gateway startup latency for subprocess tests

Gateway subprocess readiness for test-launched instances improved by moving non-essential startup work off the blocking lifespan path:

- interrupted YouTube startup recovery now runs in the background
- YouTube playlist watcher startup now runs in the background
- AgentMail startup and its recovery sweep now run in the background
- Google Workspace listener startup now runs in the background

This reduced `/api/v1/health` readiness time enough for cron/heartbeat subprocess tests to proceed.

### 4. Order-dependent test contamination

Several gateway tests were failing because they leaked process-global state into later tests. The main patterns fixed were:

- `sys.modules` contamination from mocked imports
- inherited real workstation/VPS environment bleeding into subprocess test gateways
- durable runtime DB state leaking across tests that expected a fresh workflow-admission state
- stale test expectations that still assumed old whitelist- or hook-path behavior

## Files Updated During This Pass

### Runtime / application code

- `src/universal_agent/gateway_server.py`
- `src/universal_agent/cron_service.py`
- `src/universal_agent/heartbeat_service.py`
- `src/universal_agent/execution_engine.py`
- `src/universal_agent/vp/worker_loop.py`

### Gateway tests

- `tests/gateway/test_todo_pipeline_integration.py`
- `tests/gateway/test_continuity_metrics.py`
- `tests/gateway/test_cron_api.py`
- `tests/gateway/test_cron_notifications.py`
- `tests/gateway/test_cron_scheduler.py`
- `tests/gateway/test_env_sanitization.py`
- `tests/gateway/test_heartbeat_delivery_policy.py`
- `tests/gateway/test_heartbeat_exec_timeout.py`
- `tests/gateway/test_ops_api.py`

### Docs

- `docs/03_Operations/107_Task_Hub_And_Multi_Channel_Execution_Master_Reference_2026-03-31.md`
- this document

## Test State At Stop Point

By the time this pass stopped:

- the early gateway suite block that had previously been failing was green again:
  - cron API
  - cron notifications
  - cron scheduler
  - heartbeat delivery policy
  - heartbeat timeout
  - heartbeat seeding
  - env sanitization
  - continuity metrics
  - VP general worker execution test
  - dashboard events cursor pagination

- the full `tests/gateway -x -vv` run had progressed far deeper into the suite than before, but it was **not completed to the end**

This means the gateway suite is in a better state than before, but not yet formally “all green”.

## Resume Instructions

When resuming this work later, do it in this order:

1. Re-run the full gateway suite:

```bash
uv run pytest tests/gateway -x -vv
```

2. Stop at the first remaining failure and fix only that failure before moving on.

3. After each fix:

```bash
uv run pytest <single failing file or test> -vv
uv run pytest tests/gateway -x -vv
```

4. Do not trust isolated greens alone; always go back to the broad `tests/gateway -x -vv` pass after each fix.

## Specific Reminder: Continue Removing Stale Or Redundant Tests

Part of the work here was not just fixing runtime bugs, but correcting tests that were stale against the current architecture. That follow-up is still important.

The main stale-test categories already encountered were:

- tests assuming old env-sanitization whitelist semantics instead of current blocklist semantics
- tests assuming older hook-dispatch interfaces
- tests relying on inherited real environment instead of explicit subprocess envs
- tests leaking mocked module state across the pytest process
- tests asserting old threshold values after policy changes

Continue auditing for these patterns:

- process-global monkeypatching that survives beyond the test
- import-time `sys.modules` replacement
- subprocess tests launched with `**os.environ` instead of a minimal deterministic env
- assertions that encode an old architectural contract instead of current code-verified behavior

Where possible:

- prefer isolated fixtures over ad hoc per-test cleanup
- prefer deterministic subprocess env builders shared within a file
- prefer single-source helpers over duplicated old expectations

## Recommended Next Work Item

Continue the gateway-suite burn-down until `tests/gateway` is fully green, then:

1. run the broader targeted suites around modified areas
2. decide which newly stabilized helpers/tests should be consolidated
3. remove or rewrite any remaining stale gateway tests that still encode superseded behavior

## Important Constraint

This debugging pass was intentionally focused on test hardening and regression recovery so regular feature work could resume. It should not be allowed to expand into broad refactoring unless a remaining failure proves the architecture itself is wrong.

The correct posture going forward is:

- resume normal development
- keep this gateway test hardening as a bounded cleanup track
- continue knocking down one failing gateway regression at a time
