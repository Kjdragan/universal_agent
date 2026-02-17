---
name: todoist-orchestration
description: Govern Todoist usage so reminders and brainstorm capture use internal Todoist tools first, while complex engineering/research work stays on the normal decomposition and specialist pipeline. Use when requests mention reminders, to-dos, brainstorm capture, backlog progression, heartbeat candidate triage, or proactive follow-up from ideas.
---

# Todoist Orchestration

Use this skill to keep Todoist as a helper lane, not the main execution lane.

## Routing Contract

1. Classify the request intent before tool calls.
2. If intent is reminder/todo/brainstorm capture, use internal Todoist tools first:
   - `mcp__internal__todoist_setup`
   - `mcp__internal__todoist_query`
   - `mcp__internal__todoist_get_task`
   - `mcp__internal__todoist_task_action`
   - `mcp__internal__todoist_idea_action`
3. If intent is complex implementation/research/build execution, do not route into Todoist bookkeeping; follow normal decomposition + specialist + Composio backbone.
4. Use Composio Todoist connector flow only when:
   - user explicitly asks for connector-level/OAuth behavior, or
   - internal Todoist tools are unavailable/failing.

## Intent Mapping

- Reminder/personal todo examples:
  - "remind me to call doctor"
  - "add groceries to my list"
  - "remember to do X this afternoon"
  -> Use Todoist task actions.

- Brainstorm/backlog examples:
  - "capture this idea"
  - "save this for later"
  - "track this as possible future work"
  -> Use `todoist_idea_action` record/promote/park/pipeline.

- Complex execution examples:
  - "research this and generate a report"
  - "implement this feature in repo"
  - "run multi-step integration workflow"
  -> Keep on decomposition lane; Todoist can be updated after execution if useful.

## Proactive Heartbeat Use

1. Preserve brainstorming ideas in Todoist with dedupe metadata.
2. Surface heartbeat candidates through heartbeat system events.
3. During heartbeat runs, prefer lightweight proactive investigation tasks for top candidates.
4. Report what was explored, what was produced, and what decisions are needed from the user.

## Guardrails

- Do not rewrite multi-phase implementation tasks into Todoist-only phases.
- Do not trigger Composio Todoist OAuth flow if internal Todoist path is healthy and fits intent.
- Keep Todoist updates concise and deterministic (dedupe keys, section transitions, labels).
- When uncertain, ask one clarifying question about intent: "capture reminder/idea" vs "execute full implementation".
