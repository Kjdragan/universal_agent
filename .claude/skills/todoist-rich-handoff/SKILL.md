---
name: todoist-rich-handoff
description: Create high-context Todoist pause/resume reminders with explicit restart docs, due time, personal-only labels, and verification that tasks are excluded from heartbeat auto-work. Use when users ask to "remind me to resume this tomorrow", "capture handoff in Todoist", or "pause now and pick up later".
---

# Todoist Rich Handoff

Use this skill to capture work-in-progress into Todoist with enough context to restart fast.

## Use This Skill When

1. The user is stopping work and wants to resume later.
2. The reminder must include restart context (status, next steps, docs, commits, run notes).
3. The reminder must remain personal-only (excluded from heartbeat auto-execution).

## Routing and Tool Policy

1. Prefer internal Todoist tools first:
   - `mcp__internal__todoist_setup`
   - `mcp__internal__todoist_task_action`
   - `mcp__internal__todoist_query`
   - `mcp__internal__todoist_get_task`
2. If internal tools are unavailable, use the bundled script:
   - `scripts/create_pause_resume_reminders.py`
3. Do not use Composio Todoist unless internal Todoist paths are unavailable and user explicitly wants connector behavior.

## Standard Contract (Default)

1. Project: `UA: Immediate Queue` (`project_key=immediate`)
2. Section: `Scheduled`
3. Labels:
   - `personal-reminder`
   - `sleep-handoff`
   - `no-auto-exec`
4. Explicit local due datetime (never vague relative-only text in final verification output).
5. Remove/avoid `agent-ready` on these reminders.
6. Add handoff comment:
   - `Resume context captured by Codex handoff`

## Context Bundle Requirements Per Task

Each reminder description should include:

1. Current status (one short paragraph).
2. Next concrete work steps.
3. Canonical file paths for restart docs.
4. Relevant commits/IDs/run references when available.

## Verification Requirements

After create/update:

1. Task appears in expected project + section.
2. Task labels do not include `agent-ready`.
3. Task ID is absent from `TodoService().get_actionable_tasks()`.
4. Due timestamp is present and concrete.

## Fast Path for Threads + Corporation Pause

Use bundled preset:

```bash
cd /home/kjdragan/lrepos/universal_agent
set -a; source /opt/universal_agent/.env; set +a
PYTHONPATH=src uv run --active python3 .claude/skills/todoist-rich-handoff/scripts/create_pause_resume_reminders.py \
  --preset threads-corp \
  --due-local "2026-03-05T09:00" \
  --timezone "America/Chicago"
```

## Custom Task Batch Format

Use `--tasks-file` JSON for arbitrary reminders:

```json
{
  "tasks": [
    {
      "content": "Resume X workstream",
      "description": "Status... Next steps... Docs..."
    }
  ]
}
```

Then run:

```bash
cd /home/kjdragan/lrepos/universal_agent
PYTHONPATH=src uv run --active python3 .claude/skills/todoist-rich-handoff/scripts/create_pause_resume_reminders.py \
  --tasks-file /tmp/reminders.json \
  --due-local "2026-03-05T09:00" \
  --timezone "America/Chicago"
```

