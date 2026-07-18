# Close-discipline anti-patterns to avoid

> Moved verbatim from `memory/HEARTBEAT.md` (R4 context diet, 2026-07-18). The
> disposition-verb list (`complete`/`review`/`block`/`park`) itself is also
> re-injected live every tick with claims by `_compose_heartbeat_prompt`'s
> `== TASK QUEUE TRIAGE ==` block — this file adds the specific failure modes
> that block does not spell out.

- **Don't claim a task and then forget to close it.** Every claimed assignment must end with either `complete`, `block`, `park`, `review`, `approve`, or `task_redirect_to` (for delegation). If your session ends with a claim still seized + in_progress, the lifecycle guardrail fires and emails the operator with `[ERROR] Execution Missing Lifecycle Mutation`. The 4 firings on 2026-05-24 were all this pattern.
- **Don't call `complete` on a sibling task and assume that closes the one you were claimed against.** Tool call arguments must match the assignment's `task_id` exactly.
- **Don't `TodoWrite` "completed" without invoking the `task_hub_task_action` tool.** TodoWrite is your internal scratchpad; it doesn't persist to the DB.
