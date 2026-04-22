---
name: exec-research-schedule
description: >
  Research executives at target companies using web search and X/Twitter intelligence,
  then schedule meetings with them on Google Calendar. Supports TWO modes: (1) Discovery
  mode — find relevant executives when none are specified, (2) Verification mode — when
  the user provides specific names, verify their details and schedule. USE THIS SKILL
  whenever the user mentions "research executives", "schedule meeting with [company]",
  "find leaders at", "set up calls with [team]", "research and book time with",
  "identify key people at [company]", "executive meeting prep", "schedule a call with
  [name] at [company]", "verify these contacts and set up a meeting", or ANY request
  that combines people research with calendar event creation. Also applies when the user
  provides a list of names to meet with and wants calendar events created with background
  research. Triggers on phrases like "find the VP of X at Y and schedule a call",
  "research who runs [product] at [company] and book time", "I want to meet with the
  [team] leads at [company]", "schedule a meeting with John Smith and Jane Doe from
  [company]". Do NOT use for simple calendar lookups without research, or for full
  briefing generation (use meeting-prep-pipeline-tf instead).
---

# Executive Research & Meeting Scheduling

**Version: v2** (added verification mode for user-provided names)

A focused skill that operates in two modes:
- **Discovery mode** — identify executives at target companies via web search
- **Verification mode** — when user provides names, verify their details and gather intel

Both modes schedule meetings on Google Calendar with attendee context in the event description.

## Goal

For each target company + product area, identify relevant executives (VP+ level,
verified through 2+ independent sources), gather brief intelligence on each, and
create calendar events with rich descriptions that include the attendee research.

## Parameters

The user should provide (or the agent should infer):
- **Target companies** — one or more company names
- **Product area** — the specific team, product, or division of interest
- **Known executives** _(optional)_ — specific names the user wants to meet with. When provided, skip discovery and go straight to verification.
- **Meeting times** — preferred date/time for each meeting (or ask the agent to find slots)
- **Meeting duration** — default 45 minutes

## Success Criteria

- At least 2 real, web-verified executives identified per target company (3 preferred)
- Each executive verified against 2+ independent sources (company site, LinkedIn, press)
- Meetings scheduled on Google Calendar via `google_calendar` skill
- Calendar event descriptions contain: executive name, role, relevance, recent activity
- No fabricated names or roles

## Constraints

- No fabricated executives. If search comes up empty, report the gap honestly.
- Calendar operations exclusively via `google_calendar` skill (gws CLI).
- Web searches append `-site:wikipedia.org -site:pinterest.com -site:quora.com`.
- Research budget: 2-3 minutes per executive, 15 minutes total per company.
- X/Twitter: use `x_trends_posts` exclusively. Never use Composio for X/Twitter.

## Approach

**Choose the mode based on whether the user provides names:**
- If the user says "meet with Boris Cherny at Anthropic" → **Verification mode** (Step 1b)
- If the user says "find executives at Anthropic's Claude Code team" → **Discovery mode** (Step 1a)
- If mixed (some names + "and find others") → do both

### Step 1a: Executive Discovery (when no names provided)

For each target company + product area:
1. Delegate research to `research-specialist` sub-agent (parallel for multiple companies)
2. Search query: `"[Company]" "[Product Area]" executive VP Director site:linkedin.com OR site:techcrunch.com`
3. Verify each person against 2+ independent sources (company site, LinkedIn, press releases, podcasts)
4. Cross-reference on X/Twitter via `x_trends_posts` for recent commentary
5. Score relevance: High (directly manages the product area), Medium (adjacent), Low (unrelated)
6. Only pursue High-relevance targets
7. Note recent departures or org changes that might affect accuracy

### Step 1b: Executive Verification (when user provides names)

For each user-provided name + company:
1. Web search: `"[Full Name]" "[Company]" role title` to confirm current position
2. Verify the person still holds the stated role (check for departures or role changes)
3. Gather 1-2 recent activity items (talks, posts, launches) from the last 6 months
4. Cross-reference on X/Twitter via `x_trends_posts` for recent commentary
5. If verification fails (person left company, wrong role, etc.), report the gap — do NOT substitute a different person without asking
6. Compile the same intelligence summary as discovery mode

### Step 2: Intelligence Summary

For each verified executive, compile:
- Full name and verified title/role
- Direct product area relevance (why this person matters)
- 1-2 recent activity items (talks, posts, launches, articles) from the last 6 months
- Source URLs for verification

### Step 3: Schedule Meetings

Using the `google_calendar` skill:
1. Check existing calendar for conflicts at requested time
2. Create event with structured description (see template in `references/event_template.md`)
3. Title format: `[Company] — [Product Area] Meeting`
4. Description includes all researched executives + talking points

## Context

- Calendar skill: `.claude/skills/google_calendar/SKILL.md`
- Research methodology: `references/research_methodology.md`
- Event description template: `references/event_template.md`
- X/Twitter intelligence: use `x_trends_posts` (preferred) or `grok-x-trends` skill (fallback)
- Timezone: infer from user context; default to CDT (America/Chicago)

### gws CLI Auth Note
If `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` is set to empty string, gws CLI will fail.
Use keyring auth by unsetting the env var, or decode the base64 credentials JSON to a file
and point the env var at it. The keyring backend is preferred. Command syntax:
```bash
# List events
npx -y @googleworkspace/cli calendar events list --params '{"calendarId":"primary",...}'
# Create event (use --params for calendarId, --json for request body)
npx -y @googleworkspace/cli calendar events insert --params '{"calendarId":"primary"}' --json '{...}'
```

## Anti-Patterns

- Don't invent executives. Empty results are acceptable; fabricated ones are not.
- Don't schedule without checking calendar conflicts first.
- Don't over-research. A 2-3 minute per-person budget produces enough for a calendar description.
- Don't use Composio for X/Twitter — use `x_trends_posts` exclusively.
- Don't hardcode meeting times. Accept user-specified times or suggest available slots.
- Don't assume org charts are static. Note recent departures or restructurings.
- In verification mode, don't silently substitute a different person if the named exec can't be verified. Report the gap and ask.
- Don't skip verification even when names are provided. The user expects confirmed details, not just a name echo.
