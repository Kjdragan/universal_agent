# TaskStop Guardrails and Task Hub Execution Hardening (2026-03-31)

> Companion explainer for the March 31 hardening work. This document teaches the concepts behind the fix and records what changed, why it mattered, and how the system behaves now.
>
> Canonical subsystem references still live in:
> - `docs/02_Subsystems/Proactive_Pipeline.md`
> - `docs/03_Operations/82_Email_Architecture_And_AgentMail_Source_Of_Truth_2026-03-06.md`
> - `docs/02_Flows/04_Chat_Panel_Communication_Layer.md`

## 1. Why This Work Mattered

The Universal Agent pipeline had a reliability problem in an especially important place: work could enter the system correctly, move into Task Hub, get claimed by Simone, and still fail because the runtime drifted into the wrong control model.

The most visible symptom was this class of failure:

- a task entered Task Hub and moved to `in_progress`
- Simone did some real work
- the run ended without a durable Task Hub lifecycle mutation
- the task stayed stuck or bounced back with an error like `Execution Missing Lifecycle Mutation`

This was damaging because it made the system look like it was "almost working" while still breaking the actual contract that matters: durable task ownership, disposition, and repeatability.

The fix was not just a bug patch. It was an architectural hardening pass:

- unify execution around the canonical Task Hub lane
- stop the model from reaching for the wrong task-control primitive
- give the model correct next-step guidance when a wrong move is attempted

## 2. The Core Concept: Two Different Task Systems

The most important idea to understand is that the codebase contains **two different notions of "task control"**.

### A. SDK / Claude task control

This is the Claude-side subtask system, where the model may use tools like:

- `Task`
- `Agent`
- `TaskStop`

These are SDK-level orchestration controls. They are about launching or stopping Claude-managed delegated work.

They are **not** the same thing as the Universal Agent's durable Task Hub.

### B. Universal Agent Task Hub control

This is the app's durable task system. It lives in SQLite and drives:

- unassigned work
- claimed work
- review state
- completion state
- delegation state

This system is mutated through Task Hub lifecycle tools such as:

- `task_hub_task_action(action="complete")`
- `task_hub_task_action(action="review")`
- `task_hub_task_action(action="block")`
- `task_hub_task_action(action="park")`
- `task_hub_task_action(action="delegate")`

When a work item is running in the canonical ToDo lane, **Task Hub is the source of truth** for the outer lifecycle.

What changed after the March 31 follow-up alignment is important:

- the durable **Task Hub work item** remains the only thing shown in the To Do List
- SDK `Task` / `Agent` delegation is again allowed as an internal execution mechanism inside `todo_execution` when the work item's execution manifest requires the golden research/report path
- `TaskStop` remains blocked because it collides with Task Hub lifecycle ownership

## 3. What Went Wrong Before

Before this hardening work, `todo_execution` runs had two problems:

### Problem 1: The model could still think in the wrong SDK-task terms

Even though the work item had already been claimed in Task Hub, the runtime still left enough room for the model to attempt Claude-side controls like `TaskStop`.

That caused bad behavior such as:

- trying to stop a Task Hub work item with an SDK stop tool
- treating `email:...` or `chat:...` IDs like Claude task IDs
- producing work products without ever recording the correct Task Hub disposition

### Problem 2: The old block message was too generic

The system could sometimes block an invalid `TaskStop`, but the corrective message was broad and weak. It said things like:

- delegate via `Task()`
- call an MCP tool
- start a search

That was not good enough for a canonical Task Hub lane. In that lane, the agent does not need vague suggestions. It needs a precise correction:

- continue execution
- or disposition the task in Task Hub

## 4. Visual Comparison: Old Path vs Hardened Path

The easiest way to see the value of this work is to compare the old failure path with the new guarded path.

### 4.1 Old failure path

```text
Email or Chat Request
        |
        v
Task Hub item created
        |
        v
Simone claims task -> task is now in_progress
        |
        v
Simone starts real work
        |
        v
Runtime drifts into SDK task-control mindset
        |
        v
TaskStop is attempted against a Task Hub work-item ID
        |
        v
Tool call errors or is semantically wrong
        |
        v
Run ends without task_hub_task_action(...)
        |
        v
Mission guardrail raises:
"Execution Missing Lifecycle Mutation"
```

### 4.2 Hardened path

```text
Email or Chat Request
        |
        v
Task Hub item created
        |
        v
Simone claims task -> task is now in_progress
        |
        v
Simone starts real work
        |
        v
If TaskStop is attempted:
  shared guardrail checks run_kind
  + checks for prior SDK Task/Agent evidence
        |
        v
Invalid TaskStop is blocked with lane-specific guidance
        |
        v
Simone is redirected to the correct action:
  continue work
  or call task_hub_task_action(complete/review/block/park)
        |
        v
Run ends with durable Task Hub mutation
        |
        v
Task remains auditable, restartable, and correctly dispositioned
```

### 4.3 What the diagrams show

The old path failed because the system allowed an execution-lane task to drift into the wrong control plane. The hardened path fixes that in two ways:

- it blocks the wrong control primitive in the wrong lane
- it teaches the runtime exactly which durable action should happen instead

## 5. The Fixes

### 5.1 A shared `TaskStop` policy

We created a single shared policy layer in:

- `src/universal_agent/task_stop_guardrails.py`

This module now owns the important `TaskStop` decisions:

- whether the `task_id` even looks legitimate
- whether the current run kind should ever allow `TaskStop`
- whether the run has evidence of real SDK `Task` / `Agent` delegation
- whether the same stop was already requested
- what corrective message should be shown if the stop is blocked

This matters because the old behavior was split across multiple places. Once the logic is centralized, policy drift becomes much less likely.

### 5.2 Run-aware blocking

The system now resolves `run_kind` from durable run state and applies lane-specific rules.

`TaskStop` is now hard-blocked in these lanes:

- `todo_execution`
- `email_triage`
- `heartbeat*`

Those are lanes where SDK task-stop behavior is either wrong or structurally outside the contract.

This is a key design decision: the system no longer treats every `TaskStop` request as a neutral tool request that merely needs ID validation. It first asks:

> "Is this the kind of run where `TaskStop` should exist at all?"

### 5.3 Evidence-based allowance in general lanes

Outside the hard-blocked lanes, the system still does not allow `TaskStop` just because an ID "looks opaque."

It now also asks:

> "Has this run actually used SDK `Task` or `Agent` delegation?"

If the run has no durable evidence of prior `Task` / `Agent` use, then `TaskStop` is blocked even in a general lane.

This closes an important loophole:

- previously: a plausible opaque ID could pass validation
- now: the run must also show evidence that there is a real SDK-managed subtask world to stop

### 5.4 Better corrective guidance

The new block messages tell the agent what it should do next.

For `todo_execution`, the message now explicitly says:

- you are already in the canonical Task Hub lane
- do not use `TaskStop`
- either continue the task or disposition it via `mcp__internal__task_hub_task_action`
- use `complete`, `review`, `block`, or `park` as appropriate

For `email_triage`, the message now explicitly says:

- this run is triage-only
- do not attempt final execution here
- finish triage and let the dedicated ToDo executor own execution

For heartbeat-style runs, the guidance points the agent back toward the canonical health/proactive workflow rather than cancellation semantics.

This is important because good guardrails do not just say "no." They redirect the model back onto the correct path.

## 6. How This Improved the Pipeline

This work improved the system in five practical ways.

### 6.1 It reduced false task-control drift

The runtime no longer confuses:

- Claude-managed subtask stopping

with:

- Task Hub lifecycle control

That separation is critical for durable execution.

### 6.2 It made Task Hub the real source of truth again

In canonical lanes, the only acceptable end states are durable Task Hub mutations such as:

- complete
- review
- block
- park
- delegate

That makes the system auditable and restartable.

### 6.3 It improved recovery behavior

When the model reaches for the wrong tool, it now gets a correction that is specific to the current lane instead of a vague generic nudge.

That increases the odds of autonomous recovery without human intervention.

### 6.4 It improved observability

Blocked `TaskStop` attempts now emit structured logging that includes:

- `run_id`
- `run_kind`
- rejected `task_id`
- rejection reason
- whether the run had prior SDK `Task` / `Agent` evidence

This means future investigations can answer "why did it try that?" from logs instead of speculation.

### 6.5 It reinforced the broader intake hardening

This work fits into the larger pipeline hardening completed at the same time:

- trusted email now defaults to one canonical Task Hub item per inbound request
- tracked chat can enter the same Task Hub lifecycle
- `todo_execution` blocks `TaskStop` as a second backstop while still allowing sanctioned SDK delegation inside the run
- redundant claim attempts are idempotent in the Task Hub bridge

Together, these changes moved the project closer to one central execution model instead of multiple partially-overlapping ones.

## 7. The New Mental Model

If you remember only one thing, remember this:

### Old mental model

"A task came in, so the model can probably use any task-like tool it sees."

### Correct mental model

"The transport can vary, but once work enters the canonical lane, Task Hub owns the lifecycle. Internal SDK delegation may still happen inside the run, but `TaskStop` and lifecycle ownership stay constrained by Task Hub."

That mental model is what makes the system repeatable.

Ingress may differ:

- email
- chat panel
- cron
- calendar

But canonical execution should converge into a small number of durable, well-defined lanes.

## 8. Where the Important Logic Lives

If you want to study this part of the system in code, start here:

- `src/universal_agent/task_stop_guardrails.py`
  - shared policy for `TaskStop`
- `src/universal_agent/hooks.py`
  - shared hook path used by the modern runtime
- `src/universal_agent/main.py`
  - legacy / parallel pre-tool path kept in sync with the shared policy
- `src/universal_agent/services/todo_dispatch_service.py`
  - canonical `todo_execution` prompt contract
- `src/universal_agent/gateway.py`
  - run-kind-specific extra disallowed tool policy
- `src/universal_agent/mission_guardrails.py`
  - enforcement that `todo_execution` must end with a durable lifecycle mutation

And for tests:

- `tests/unit/test_hooks_task_stop_guardrail.py`
- `tests/unit/test_main_pretool_taskstop_guardrail.py`

## 9. Lessons Learned

This work reinforced several reusable design lessons.

### Lesson 1: Similar words can hide different systems

`TaskStop` sounds like it should stop a task. But in this codebase, "task" can mean:

- a Claude SDK subtask
- a durable Task Hub item

Never assume those are interchangeable.

### Lesson 2: Good guardrails are semantic, not just syntactic

It is not enough to validate whether an ID looks real.

The system also needs to ask:

- is this the right lane for this tool?
- is there evidence that this tool should exist in this run?

That is the difference between pattern matching and architectural guardrails.

### Lesson 3: The best correction is the next correct action

The most effective guardrails do two things:

- prevent the wrong move
- teach the correct move

This is especially important in agentic systems, because vague "don’t do that" messages often just cause the model to drift into a different wrong action.

## 10. Bottom Line

This hardening work made the project safer and more coherent.

Before:

- canonical Task Hub work could drift into the wrong task-control model
- `TaskStop` could appear in runs where it was conceptually wrong
- the model could do real work but still fail the durable lifecycle contract

After:

- canonical lanes explicitly reject `TaskStop`
- general lanes only allow it when there is durable evidence of real SDK delegation
- blocked calls return lane-specific corrective guidance
- Task Hub execution is better protected from control-plane drift

That is a meaningful architectural improvement, not just a narrower test fix.
