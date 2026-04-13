---
name: google_calendar
description: Manage calendar events using the Google Workspace CLI (gws) natively. Use when the user asks to check schedule, create events, or manage calendar.
metadata: {"requires": ["gws"]}
---

# Google Calendar Skill (via gws CLI)

You can interact with Google Calendar using the `gws` command via the Bash tool. The command is executed via `npx -y @googleworkspace/cli` to ensure it works even if not globally linked.

## Tools (CLI commands)

- `npx -y @googleworkspace/cli calendar list` — List calendars.
- `npx -y @googleworkspace/cli calendar get --id "calendar_id"` — Get details for a specific calendar.
- `npx -y @googleworkspace/cli calendar events list --calendarId "primary"` — List upcoming events.
- `npx -y @googleworkspace/cli calendar events insert --calendarId "primary" --summary "Meeting" --start "2026-03-07T10:00:00-06:00" --end "2026-03-07T11:00:00-06:00"` — Create a new event.

## Usage Guidelines

1.  **Authentication**: Handled by gws auth. Ensure `UA_ENABLE_GWS_CLI=1` is set.
2.  **Date/Time**: Use ISO 8601 format (e.g., `2026-03-07T10:00:00-06:00`).
3.  **Timezone**: Be aware of the user's timezone (check context). Include timezone offset in datetime strings.
4.  **NEVER attempt to use `mcp__gws__*` tools or Composio tools for Calendar. ALWAYS use the raw `npx @googleworkspace/cli` commands via Bash.**

## Workflows

- **Schedule Meeting**: Check availability via `events list` → `events insert` to create event → confirm with user.
- **Daily Briefing**: `events list` for today's events → summarize for user.
- **Recurring Events**: Use `events insert` with recurrence rules (RRULE format) if possible.
