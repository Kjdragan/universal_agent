---
name: google_calendar
description: Manage calendar events using the Google Workspace CLI (gws) natively. Use when the user asks to check schedule, create events, or manage calendar.
metadata: {"requires": ["gws"]}
---

# Google Calendar Skill (via gws CLI)

Interact with Google Calendar using `npx -y @googleworkspace/cli` via the Bash tool.

## Critical: Authentication

**Before ANY gws calendar command**, unset the credentials env var and use keyring:
```bash
unset GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE && npx -y @googleworkspace/cli calendar events list --params '{"calendarId":"primary"}'
```

> **Why:** If `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` is set (even to an empty string), gws tries to use it as a file path and fails. The keyring backend (stored in `~/.config/gws/`) works reliably. Always `unset` first.

## CLI Syntax (IMPORTANT — read carefully)

The gws CLI uses **two** JSON arguments:
- `--params` — URL/query parameters (e.g., `calendarId`, `timeMin`, `timeMax`)
- `--json` — Request body (e.g., `summary`, `description`, `start`, `end`)

**Do NOT use** `--calendarId`, `--summary`, `--start`, etc. as direct flags — those are deprecated and will error.

## Commands

### List events
```bash
unset GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE && npx -y @googleworkspace/cli calendar events list \
  --params '{"calendarId":"primary","timeMin":"2026-04-21T00:00:00-05:00","timeMax":"2026-04-25T23:59:59-05:00","singleEvents":true}'
```

### Create event
```bash
unset GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE && npx -y @googleworkspace/cli calendar events insert \
  --params '{"calendarId":"primary"}' \
  --json '{
    "summary": "Meeting Title",
    "description": "Meeting description with details",
    "start": {"dateTime": "2026-04-23T10:00:00-05:00", "timeZone": "America/Chicago"},
    "end": {"dateTime": "2026-04-23T10:45:00-05:00", "timeZone": "America/Chicago"}
  }'
```

### Delete event
```bash
unset GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE && npx -y @googleworkspace/cli calendar events delete \
  --params '{"calendarId":"primary","eventId":"EVENT_ID_HERE"}'
```

### List calendars
```bash
unset GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE && npx -y @googleworkspace/cli calendar list
```

## Usage Guidelines

1.  **Authentication**: Always `unset GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` before any command. Keyring handles auth.
2.  **Date/Time**: Use ISO 8601 format with timezone offset (e.g., `2026-04-23T10:00:00-05:00`).
3.  **Timezone**: Default to `America/Chicago` (CDT = -05:00). Include `timeZone` in start/end objects.
4.  **NEVER attempt to use `mcp__gws__*` tools or Composio tools for Calendar. ALWAYS use the raw `npx @googleworkspace/cli` commands via Bash.**
5.  **Check `--help`**: If a command fails, run `npx -y @googleworkspace/cli calendar events insert --help` to verify syntax.

## Workflows

- **Schedule Meeting**: Check availability via `events list` → `events insert` to create event → confirm with user.
- **Daily Briefing**: `events list` for today's events → summarize for user.
- **Recurring Events**: Use `events insert` with recurrence rules (RRULE format) via `--json`.
