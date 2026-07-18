# Activating `/goal` for Cody delegations

> Moved verbatim from `memory/HEARTBEAT.md` (R4 context diet, 2026-07-18). Read this
> whenever you're delegating to Cody (`vp.coder.primary`) and need to decide whether
> to request the `/goal` loop.

When delegating to Cody (`vp.coder.primary`) for work with a **verifiable end state** — tests passing, lint clean, a PR opened, a specific file present — request the `/goal` loop. The loop drives Cody across multiple turns until a Haiku evaluator confirms the condition holds, without per-turn operator nudging.

**When `/goal` is activated automatically (no action required from you):**

| Source | Mechanism |
|---|---|
| `cody_demo_task`, `cody_scaffold_request`, `tutorial_build` | Always /goal-eligible by source_kind (PRD § 3 decision 1) |
| **Dashboard "Dispatch Mission" UI targeting Cody** | The endpoint sets `metadata.use_goal_loop=True` on the task hub item; `vp_dispatch_mission` inherits it onto the mission. Verified at `gateway_server.py:dashboard_todolist_quick_add`. |

**When you (Simone) should set it explicitly:**

You may set `use_goal_loop=True` when delegating to Cody for work whose success has a transcript-observable end state. Pass it via the metadata dict:

```text
vp_dispatch_mission(
    vp_id="vp.coder.primary",
    objective="<crisp objective with verifiable success criteria>",
    mission_type="task",
    task_id="<source_task_id>",          ← REQUIRED for /goal flow inheritance
    idempotency_key="task-<task_id>",
    metadata={"use_goal_loop": True},
)
```

> **`task_id` is REQUIRED, not optional**, whenever you're dispatching an
> operator-dispatched task (i.e., a task hub item where Kevin typed the
> objective into the dashboard's Dispatch Mission box). The `vp_dispatch_mission`
> tool uses `task_id` to look up the linked task hub row and propagate
> `metadata.use_goal_loop=True` onto the spawned VP mission. Without `task_id`,
> the inheritance never fires and the mission runs WITHOUT the /goal loop —
> even if you also set `metadata={"use_goal_loop": True}` (which works too,
> but `task_id` is the single source of truth that all downstream surfaces
> rely on, including the dashboard's `goal-artifacts` panel that needs to
> trace the original prompt back from the mission).
>
> **Passing `idempotency_key="task-<task_id>"` does NOT substitute for `task_id`.**
> idempotency_key is purely for dispatch dedup; the inheritance code reads
> `args.get("task_id")` (or `metadata.task_id`), not the idempotency key.

**When NOT to set it:**

- Atlas missions (`vp.general.primary`) — `/goal` is Cody-only and is silently ignored on Atlas
- `proactive_codie` cleanup — that's a search task ("find SOMETHING worth improving"), not a goal task
- Exploratory or open-ended Cody work without a clear "done" condition — `/goal`'s evaluator needs an end state to judge

**Operator overrides:**

If Kevin tells you "use /goal for this" or "set up a goal loop" in chat, set `metadata={"use_goal_loop": True}` regardless of source_kind. Operator intent wins.
