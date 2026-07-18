# How to delegate cleanly

> Moved verbatim from `memory/HEARTBEAT.md` (R4 context diet, 2026-07-18). Read this
> whenever you're about to delegate a task to Atlas or Cody via `vp_dispatch_mission`.

When you decide to delegate a task to Atlas or Cody:

1. Call `vp_dispatch_mission(objective=..., target_vp="vp.general.primary"|"vp.coder.primary", task_id=...)` with a natural-language objective that captures **what done looks like**. Include the source `task_id` so the VP can reference it in its own assignment and so you can correlate later.
2. **Release your claim on the source task.** Today, the cleanest verb is `task_redirect_to(task_id, target_vp="vp.general.primary"|"vp.coder.primary", reason="delegated via vp_dispatch_mission")`. That clears your retry counters and stamps `metadata.preferred_vp` so the lifecycle audit doesn't fire a "missing lifecycle mutation" guardrail. **Do not** call `complete` on the source task — the work isn't done yet; the VP will close it.
3. Move on. Don't keep mental state on the delegated task. **When the VP succeeds, the task closes automatically** — you do NOT need to review or sign off on routine successes. The VP emails Kevin directly and CCs you for situational awareness only; that CC is your visibility, not your action item. (Earlier versions of this doc promised a `needs_review` pause for sign-off; that pause was never built and was removed from the architecture by design — per-task review by Simone was rejected to preserve cap-of-1 throughput. See [`project_docs/03_agents/01_vp_workers_and_delegation.md`](../../project_docs/03_agents/01_vp_workers_and_delegation.md).)
4. **When the VP fails, the failure surfaces as a `vp_mission_failure` informational task hub item** in your queue. In that posture you are the rescue-evaluator — you choose one of: retry-with-guidance, redispatch-fresh, escalate-to-operator, or ignore (let the failure stay in your context for next-occurrence escalation). The rescue tools and decision tree are documented in the failure-rescue PR's HEARTBEAT addendum (see `memory/reference/vp-mission-failure-rescue.md`).

If a task is small enough that you'll execute it yourself, the close discipline is the standard one: do the work, then **explicitly call** `task_hub_task_action(action="complete", task_id="...")` — not a `TodoWrite` claim that you completed it. The guardrail reads the live DB, not your internal todo list.
