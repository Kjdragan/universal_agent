# Calendar Event Description Template

Use this template for the `description` field when creating calendar events.

```
Executive Meeting — [Company] [Product Area]

Attendees Researched:
─────────────────────
1. [Name] — [Title]
   Role: [one-line relevance to product area]
   Recent: [1-2 activity items from last 6 months]
   Sources: [verification source names]

2. [Name] — [Title]
   Role: [one-line relevance]
   Recent: [activity items]
   Sources: [verification sources]

3. [Name] — [Title]
   ...

Talking Points:
───────────────
- [Point 1 derived from executive's recent public statements or product launches]
- [Point 2 relevant to their area of responsibility]
- [Point 3 competitive or industry context]

Notes:
──────
Research conducted [date]. Executives verified via [list sources].
[Note any recent org changes, departures, or restructuring that may affect accuracy.]
```

## gws CLI Event Creation Syntax

```bash
npx -y @googleworkspace/cli calendar events insert \
  --params '{"calendarId":"primary"}' \
  --json '{
    "summary": "[Company] — [Product Area] Meeting",
    "description": "<filled template above>",
    "start": {"dateTime": "2026-04-23T10:00:00-05:00", "timeZone": "America/Chicago"},
    "end": {"dateTime": "2026-04-23T10:45:00-05:00", "timeZone": "America/Chicago"}
  }'
```

Note: Use `--params` for the calendarId and `--json` for the request body. Do not put start/end in params.
