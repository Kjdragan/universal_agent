# System Command Action Envelope (Phase 0 Spec)

Last updated: 2026-02-24

## Input Payload (UI -> Gateway)

```json
{
  "text": "schedule review this tutorial tonight at 2am",
  "source_page": "/dashboard/tutorials",
  "source_context": {},
  "timezone": "UTC",
  "dry_run": false
}
```

## Interpreted Envelope (Gateway internal)

```json
{
  "lane": "system",
  "intent": "status_query|capture_idea|capture_task|schedule_task",
  "content": "review this tutorial",
  "schedule_text": "tonight at 2am",
  "priority": "low|medium|high|urgent",
  "section": "background|scheduled",
  "source_page": "/dashboard/tutorials",
  "source_context": {}
}
```

## Output Payload (Gateway -> UI)

```json
{
  "ok": true,
  "lane": "system",
  "intent": "schedule_task",
  "interpreted": {
    "content": "review this tutorial",
    "schedule_text": "tonight at 2am",
    "priority": "medium",
    "section": "scheduled",
    "source_page": "/dashboard/tutorials",
    "source_context": {}
  },
  "todoist": { "task": { "id": "..." } },
  "cron": { "job": { "job_id": "..." } },
  "dry_run": false
}
```

## Validation Rules

1. `text` is required and non-empty.
2. `source_context` must be an object if provided.
3. `timezone` defaults to `UTC` if omitted.
4. `dry_run=true` must not mutate Todoist/Chron state.
5. `schedule_task` with cron bridge enabled may create both Todoist task and Chron job.

