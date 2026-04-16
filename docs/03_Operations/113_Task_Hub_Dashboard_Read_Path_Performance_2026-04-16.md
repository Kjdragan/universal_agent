# Task Hub Dashboard Read Path Performance (2026-04-16)

## Summary

The dashboard responsiveness issue was caused by normal read requests doing queue-maintenance work. The To Do List page loads several Task Hub endpoints in parallel. Before this change, those GET paths repeatedly rebuilt the dispatch queue while holding the gateway activity-store lock.

Queue rebuilding is expensive because it scans active tasks, scores them, updates task rows, records evaluation rows, deletes and reinserts the dispatch queue snapshot, and commits the result. Running that work during page navigation made ordinary reads compete with dispatcher writes and background maintenance.

## Root Cause

The problem was not only a lock held across `await`. The remaining bottleneck was synchronous SQLite mutation under `_activity_store_lock` in read-oriented dashboard handlers.

The most important call chain was:

1. `/api/v1/dashboard/todolist/overview`
2. `task_hub.overview(...)`
3. `get_dispatch_queue(...)`, `list_agent_queue(...)`, and `get_agent_activity(...)`
4. implicit `rebuild_dispatch_queue(...)` calls from those read helpers

One overview request could rebuild the dispatch queue multiple times. The page also fetched agent queue and activity endpoints in parallel, which multiplied the serialized work.

## Changed Behavior

Dashboard read paths now read the latest dispatch queue snapshot instead of rebuilding it:

- `get_dispatch_queue(...)` returns the latest stored snapshot, or an empty snapshot if none exists.
- `list_agent_queue(...)` no longer rebuilds the queue before returning task rows.
- `get_agent_activity(...)` no longer indirectly rebuilds through `list_agent_queue(...)`.
- `/api/v1/dashboard/todolist/overview`, `/agent-queue`, `/agent-activity`, and `/dispatch-queue` no longer rebuild queues during normal GET navigation.

Queue rebuilds remain in correctness and maintenance paths:

- dispatcher claim flow via `claim_next_dispatch_tasks(...)`
- direct claim/action/finalize and delegation lifecycle mutations
- explicit `/api/v1/dashboard/todolist/dispatch-queue/rebuild`
- other write-side maintenance flows that intentionally refresh dispatch state

## Implementation Notes For Review

The code change is intentionally small. It removes automatic queue rebuilds from read paths, rather than adding timeouts or another defensive lock.

Changed code:

- `src/universal_agent/task_hub.py`
  - `get_dispatch_queue(...)` now reads the latest stored queue snapshot. If no queue snapshot exists, it returns an empty queue payload instead of triggering a rebuild from a read path.
  - `list_agent_queue(...)` no longer calls `rebuild_dispatch_queue(...)` before querying task rows.
  - `reconcile_task_lifecycle(...)` now accepts `rebuild_queue: bool = True`. Existing callers keep the previous behavior by default. The dashboard `agent-queue` read path opts out so lifecycle repair does not force dispatch queue rebuilds during navigation.

- `src/universal_agent/gateway_server.py`
  - `dashboard_todolist_overview()` no longer explicitly rebuilds the queue before calling `task_hub.overview(...)`.
  - `_task_hub_supervisor_snapshot()` no longer rebuilds the queue as part of the read snapshot.
  - `dashboard_todolist_agent_queue(status="all")` still performs lifecycle reconciliation, but passes `rebuild_queue=False`.
  - `dashboard_todolist_dispatch_queue()` reads the current queue snapshot without rebuilding.
  - `dashboard_todolist_dispatch_queue_rebuild()` remains the explicit rebuild endpoint.

Changed tests:

- `tests/gateway/test_dashboard_agent_queue.py`
  - Adds regression coverage proving the dashboard overview, agent queue, and agent activity read endpoints do not call `rebuild_dispatch_queue(...)`.
  - Adds coverage proving the explicit rebuild endpoint still does call `rebuild_dispatch_queue(...)`.

- `tests/unit/test_task_hub_lifecycle.py`
  - Adds coverage proving `task_hub.overview(...)` can read an existing queue snapshot without mutating task rows or appending evaluation rows.
  - Adds coverage proving `claim_next_dispatch_tasks(...)` still rebuilds before claiming work.

- `tests/unit/test_dispatch_service.py`
  - Adds coverage proving `dispatch_sweep(...)` still rebuilds before claiming via the Task Hub claim path.

Documentation updates:

- `docs/02_Subsystems/Task_Hub_Dashboard.md` now states the read-path invariant.
- `docs/README.md` and `docs/Documentation_Status.md` index this document.

## Invariant For Future Changes

Do not add `rebuild_dispatch_queue(...)` back into dashboard GET endpoints or helper functions that are used by dashboard GET endpoints.

If a UI needs a fresher queue, use the explicit rebuild endpoint or a write-side maintenance path. Reads should be fast, bounded, and safe to run repeatedly during navigation and polling.

## What Not To Reintroduce

Do not fix dashboard slowness by raising the Next.js proxy timeout. That only gives slow serialized rebuilds more time to stack up. The root fix is that dashboard reads should not do dispatch queue writes.

Do not make `get_dispatch_queue(...)` rebuild automatically when the snapshot is empty. That is convenient, but it recreates the same failure mode on fresh databases and during navigation. If a fresh queue is required, call an explicit write/maintenance path first.

Do not call `rebuild_dispatch_queue(...)` from `overview(...)`, `list_agent_queue(...)`, or `get_agent_activity(...)`. Those helpers are used by high-frequency dashboard reads.

## Verification

Regression tests now assert that dashboard read endpoints do not call `rebuild_dispatch_queue(...)`, while the explicit rebuild endpoint and claim path still do.

Targeted verification used:

```bash
PYTHONPATH=src uv run pytest tests/gateway/test_dashboard_agent_queue.py tests/gateway/test_todo_dispatch_service.py tests/unit/test_task_hub_lifecycle.py tests/unit/test_dispatch_service.py -q
```

Result: `62 passed`.

Frontend lint also passed:

```bash
cd web-ui && npm run lint
```

Result: `0 errors`; existing warnings remain unrelated to this fix.

The requested frontend Vitest navigation regression did not run in this local environment. `web-ui/package.json` has no `test` script, and direct `npx vitest run app/dashboard/navigation-regression.test.tsx` fails during Vitest startup under Node `v20.12.2` because the installed `rolldown` path calls `util.styleText()` with an array format this Node build rejects. No frontend test assertions executed.
