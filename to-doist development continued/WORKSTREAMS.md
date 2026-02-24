# Workstreams and Technical Scope

Last updated: 2026-02-24

## Workstream A: System Command UX (Non-Chat Tabs)

1. Build one reusable system command input component.
2. Inject page context (`selected_run_path`, `selected_job_id`, etc.).
3. POST to a single interpreter endpoint.
4. Show immediate interpreted action summary/result.

## Workstream B: Intent Interpreter and Routing

1. Parse NL to deterministic envelope:

```json
{
  "lane": "system",
  "intent": "schedule_task|capture_idea|run_now|status_query|config_change",
  "text": "...",
  "target_ref": "...",
  "schedule_text": "tonight 2am",
  "priority": "low|medium|high|urgent",
  "manual_gate": false,
  "source_page": "dashboard/tutorials",
  "source_context": {}
}
```

2. Route envelope:
- Todoist quick add/create/update
- Chron create/update/run-now
- Ops/config endpoints where allowed

## Workstream C: Todoist Canonical Task Lifecycle

1. Capture in Todoist with deterministic labels/sections.
2. Record mapping metadata:
- `ua_task_type`
- `ua_source`
- `ua_target_ref`
- `ua_cron_job_id` (optional)
- `ua_manual_gate`
3. Update status/comments after run completion/failure.

## Workstream D: Heartbeat Proactive Executor

1. Add explicit backlog advancement objective into heartbeat mission guidance.
2. At each tick:
- evaluate idle/capacity
- fetch eligible Todoist tasks
- select one by priority + mission fit
- execute with safe guardrails
3. Publish result notification for each independent run.

## Workstream E: Daily 7:00 AM Autonomous Briefing

1. Chron job scheduled daily at 07:00.
2. Aggregate previous 24h independent executions.
3. Produce artifact report + dashboard notification.
4. Include:
- task name/id
- why selected
- outcome
- artifacts/links
- failures/blocks needing user intervention

## Workstream F: Observability and Controls

1. Store command routing decisions in structured logs.
2. Add dashboard view for system command history (phase 2+).
3. Add feature flags for staged rollout and rapid disable.

