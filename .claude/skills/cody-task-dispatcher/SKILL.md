---
name: cody-task-dispatcher
description: >
  Simone's Phase 2 skill. Once a demo workspace has been scaffolded by
  `cody-scaffold-builder` AND Simone has refined the BRIEF/ACCEPTANCE/
  business_relevance prose, this skill enqueues a `cody_demo_task` Task
  Hub item pointing Cody at the workspace. Idempotent across re-dispatches
  with the same (entity_slug, demo_id). Persistent queue policy — Cody
  picks up the task whenever she's available, never times out. Also
  supports re-issuance with FEEDBACK.md after a Phase 4 iteration. USE
  after `cody-scaffold-builder` and after Simone has reviewed the
  workspace artifacts.
---

# cody-task-dispatcher

> **Phase 2 of the ClaudeDevs Intel v2 pipeline.** Pairs with
> `cody-scaffold-builder` — that skill creates the workspace, this one
> queues Cody on it.
>
> See [v2 design doc §7.4](../../docs/proactive_signals/claudedevs_intel_v2_design.md)
> for the queueing contract.

## When to use

- A demo workspace exists at `/opt/ua_demos/<demo-id>/` (output of
  `cody-scaffold-builder`).
- Simone has reviewed and refined `BRIEF.md`, `ACCEPTANCE.md`, and
  `business_relevance.md` — no `_(Simone: ...)_` placeholders remain.
- Simone is ready to hand off to Cody.

Also use to **re-issue** a task after a Phase 4 iteration: Simone wrote
`FEEDBACK.md`, the task should be re-queued with `iteration > 1`.

## What this skill does

The `services/cody_dispatch.dispatch_cody_demo_task` function:

1. Builds a stable, deterministic task_id from `(entity_slug, demo_id)`
   so re-dispatch is idempotent.
2. Upserts a Task Hub item with `source_kind=cody_demo_task`, priority 4
   (highest), `agent_ready=True`, `queue_policy=wait_indefinitely`.
3. Sets the metadata so Cody (when she picks it up) knows the workspace
   path, entity path, endpoint requirement, iteration count, and wall-time
   max (default 30 minutes).
4. Mirrors the task as a `cody_demo_task` proactive artifact for dashboard
   visibility.

The Python performs no LLM calls. It is pure Task Hub + dashboard plumbing.

## How to invoke

From a Simone session:

```python
from pathlib import Path
import sqlite3
from universal_agent.services.cody_dispatch import dispatch_cody_demo_task
from universal_agent.activity_db import get_activity_db_path

with sqlite3.connect(get_activity_db_path()) as conn:
    conn.row_factory = sqlite3.Row
    task = dispatch_cody_demo_task(
        conn,
        workspace_dir=Path("/opt/ua_demos/skills__demo-1"),
        entity_slug="skills",
        entity_path=Path("artifacts/knowledge-vaults/claude-code-intelligence/entities/skills.md"),
        demo_id="skills__demo-1",
        endpoint_required="anthropic_native",
        wall_time_max_minutes=30,
        iteration=1,
    )
    print(task["task_id"], task["status"])
```

For a re-issue after Phase 4 feedback, use `reissue_cody_demo_task_with_feedback`:

```python
from universal_agent.services.cody_dispatch import reissue_cody_demo_task_with_feedback

task = reissue_cody_demo_task_with_feedback(
    conn,
    workspace_dir=Path("/opt/ua_demos/skills__demo-1"),
    entity_slug="skills",
    entity_path=Path("artifacts/knowledge-vaults/claude-code-intelligence/entities/skills.md"),
    demo_id="skills__demo-1",
    feedback_path=Path("/opt/ua_demos/skills__demo-1/FEEDBACK.md"),
    iteration=2,
)
```

## Idempotency guarantee

Same `(entity_slug, demo_id)` always produces the same `task_id`. Calling
the dispatcher twice with the same inputs:

1. First call: creates the Task Hub row.
2. Second call: upserts (resets status to OPEN, refreshes description,
   bumps iteration if changed). No duplicate row.

This means re-running this skill is safe — it cannot accidentally queue
two tasks for the same demo.

## Persistent queue policy

`metadata.queue_policy = "wait_indefinitely"`. The Task Hub will hold the
task until Cody is available; it will not time out, retry, or give up. If
Cody is busy for hours/days, the task simply waits.

This is intentional per the v2 design — demos are reference implementations
for client work, accuracy matters more than throughput.

## Off switch

If Cody dispatch needs to pause across the board (e.g., during a Cody
deployment incident), use Task Hub's existing pause mechanisms — this
skill itself has no off switch.

## What this skill does NOT do

- It does NOT verify the workspace contents are ready. Simone is expected
  to have reviewed BRIEF/ACCEPTANCE/business_relevance before dispatch.
- It does NOT trigger Cody directly. Task Hub's normal dispatch loop picks
  up `agent_ready=True` items.
- It does NOT modify the workspace. Read-only with respect to disk.
- It does NOT do Phase 4 review work — that's `cody-work-evaluator`.

## Related skills

- `cody-scaffold-builder` — runs BEFORE this skill.
- `cody-progress-monitor` — Phase 4 monitoring of in-flight tasks.
- `cody-work-evaluator` — Phase 4 judgment of returned artifacts.
- `vault-demo-attach` — Phase 4 attachment of passing demos to the vault.
