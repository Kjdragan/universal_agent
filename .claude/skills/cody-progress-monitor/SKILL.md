---
name: cody-progress-monitor
description: >
  Simone's Phase 4 monitoring skill. Lists every cody_demo_task in Task
  Hub with its current state (open / in-progress / blocked / completed /
  parked) and surfaces stuck or long-running tasks so Simone knows when
  to intervene. Read-only — does not modify Task Hub or workspaces.
  USE when Simone wants a quick situational read on Phase 3 demo
  execution before doing evaluator work.
---

# cody-progress-monitor

> **Phase 4 of the ClaudeDevs Intel v2 pipeline (read-only piece).**
> Pairs with `cody-work-evaluator` (judgment) and `vault-demo-attach`
> (mutation).

## When to use

- Simone wakes up on a heartbeat and wants to know: which demos are in
  flight? Which are stuck? Which are waiting for her review?
- Before invoking `cody-work-evaluator` on a specific demo to make sure
  the workspace state matches the Task Hub state.
- Periodic situational read at the start of a Phase 4 review pass.

## How to invoke

```python
import sqlite3
from universal_agent.activity_db import get_activity_db_path
from universal_agent.services.cody_evaluation import monitor_demo_tasks

with sqlite3.connect(get_activity_db_path()) as conn:
    conn.row_factory = sqlite3.Row
    rows = monitor_demo_tasks(conn)
    for row in rows:
        print(
            f"{row['status']:>15s}  iter={row['iteration']}  "
            f"prio={row['priority']}  {row['demo_id']}  "
            f"({row['entity_slug']})"
        )
```

## What this skill does

`monitor_demo_tasks(conn)` queries Task Hub for every row with
`source_kind=cody_demo_task` and returns hydrated dicts with:

- `task_id`, `title`, `status`, `priority`
- `demo_id`, `entity_slug`, `workspace_dir`
- `iteration` (1 = first attempt; >1 = post-feedback)
- `endpoint_required`, `queue_policy`
- `created_at`, `updated_at`

Read-only — no Task Hub writes, no workspace edits, no LLM calls.

## What Simone does with this

Look for:

- **`status=open`, `iteration=1`** — fresh dispatch, Cody hasn't picked
  it up yet. Wait. (queue_policy=wait_indefinitely is by design.)
- **`status=in_progress`** — Cody is working. Don't interrupt.
- **`status=needs_review` or `pending_review`** — Cody returned output;
  invoke `cody-work-evaluator` next.
- **`status=open`, `iteration>1`** — feedback loop active; Cody
  re-dequeued after Simone's previous FEEDBACK.md.
- **`status=blocked`** — Cody hit something she can't resolve. Read
  BUILD_NOTES.md in the workspace; this is operator territory, surface
  to Kevin if needed.
- **`status=parked`** — Simone previously deferred this. Will not
  auto-resume; resurrect by re-dispatching via `cody-task-dispatcher`.
- **`status=completed`** — past wins; consider re-validation if the
  underlying SDK has bumped multiple versions since then (PR 18 work).

## Related skills

- `cody-task-dispatcher` (PR 8) — what creates these rows.
- `cody-work-evaluator` (PR 10) — Simone's mutation skill for
  judging completed demos.
- `vault-demo-attach` (PR 10) — Simone's helper for landing a passing
  demo back into the vault.
