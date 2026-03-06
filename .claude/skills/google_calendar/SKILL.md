---
name: google_calendar
description: Manage calendar events using Google Calendar via gws MCP tools. Use when the user asks to check schedule, create events, or manage calendar.
metadata: {"requires": ["gws"]}
---

# Google Calendar Skill (via gws MCP)

Calendar operations are handled through **gws MCP tools** (`mcp__gws__*`). These tools use the Google Workspace CLI for native Calendar API access.

## Tools

- `mcp__gws__calendar.+agenda` — View upcoming events across all calendars (helper)
- `mcp__gws__calendar.+insert` — Create event with simplified args (helper)
- `mcp__gws__calendar.events.list` — List events with full parameter control
- `mcp__gws__calendar.events.insert` — Create events with full parameter control
- `mcp__gws__calendar.events.update` — Modify existing events
- `mcp__gws__calendar.events.delete` — Remove events

## Usage Guidelines

1.  **Authentication**: Handled by gws auth. Ensure `UA_ENABLE_GWS_CLI=1` is set.
2.  **Date/Time**: Use ISO 8601 format (e.g., `2026-03-07T10:00:00-06:00`).
3.  **Timezone**: Be aware of the user's timezone (check context). Include timezone offset in datetime strings.
4.  **Helpers vs Raw API**: Use `+agenda` and `+insert` helpers for common operations. Use raw API tools for advanced parameters.

## Workflows

- **Schedule Meeting**: check `+agenda` for availability → `+insert` to create event → confirm with user.
- **Daily Briefing**: `+agenda` for today's events → summarize for user.
- **Recurring Events**: Use `events.insert` with recurrence rules (RRULE format).
