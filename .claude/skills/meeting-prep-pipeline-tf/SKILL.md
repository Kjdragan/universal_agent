---
name: meeting-prep-pipeline-tf
description: >
  Research executives at target companies, schedule meetings on Google Calendar, then generate
  1-page executive briefings for all upcoming meetings this week. Each briefing includes attendee
  profiles, company context, talking points, and recent news. USE THIS SKILL whenever the user
  mentions "meeting prep", "executive briefing", "prepare for my meetings", "research attendees",
  "meeting intelligence", "brief me on my meetings", "pre-meeting research", "schedule meetings
  with executives", "who am I meeting with", "research and schedule", "meeting readiness",
  "get me ready for", or any request that combines executive research WITH calendar scheduling
  or briefing generation. Also use when the user wants to research people before meetings,
  prepare talking points for upcoming calls, or get intelligence on meeting attendees, even if
  they don't explicitly say "meeting prep" — if the intent is meeting preparation, this skill
  applies. Do NOT use for simple calendar lookups without a research/briefing component.
---

# Meeting Prep Pipeline

**Version: v1** (polished from v0 scaffold)

A two-phase compound skill: (1) research executives and schedule meetings,
(2) generate executive briefings for all upcoming calendar events.

## Goal

Deliver a complete meeting intelligence package: scheduled meetings with verified
executives on Google Calendar, plus a 1-page executive briefing (markdown) for every
briefing-worthy upcoming meeting this week.

## Success Criteria

- At least 2 real, web-verified executives identified per target company
- Meetings scheduled on Google Calendar via the `google_calendar` skill
- One markdown briefing per briefing-worthy upcoming event
- Each briefing has all 5 sections: profiles, context, talking points, news, openers
- All outputs saved to `work_products/briefings/`

## Phase 1: Research & Schedule

### Step 1: Executive Research

For each target company, follow the methodology in `references/research_methodology.md`:
1. Web search to identify executives in the relevant product area (VP+ level).
2. Verify each person against 2+ independent sources.
3. Cross-reference on X/Twitter via `x_trends_posts` for recent activity.
4. Score relevance (High/Medium/Low) and only pursue High-relevance targets.

### Step 2: Schedule Meetings

Using the `google_calendar` skill:
1. Check existing calendar for conflicts.
2. Find available 45-minute slots within business hours (9am-6pm user's local time).
3. Create events titled: `[Company] - [Executive Name] Meeting`
4. Include verified role and brief context in the event description.
5. Confirm each event with the user before finalizing.

## Phase 2: Meeting Briefings

### Step 1: Scan Calendar

Use `google_calendar` to list events from now through end of Friday this week.
Filter to briefing-worthy events only (see scope in `references/research_methodology.md`).

### Step 2: Generate Briefings

For each qualifying event, follow the template in `references/briefing_template.md`.
Research each attendee (2-3 min per person max). Produce a concise, scannable briefing.

### Step 3: Save Outputs

Save to `work_products/briefings/YYYY-MM-DD_[sanitized-event-title].md`.

## Constraints

- No fabricated names. Unverifiable people get flagged, not invented.
- Calendar operations exclusively via `google_calendar` skill.
- Briefings must be under 1 printed page (concise > comprehensive).
- Web searches append `-site:wikipedia.org -site:pinterest.com -site:quora.com`.
- Total research time budget: 15 min for Phase 1, 5 min per briefing for Phase 2.

## Composed Skills

- `google_calendar` — calendar reads and event creation
- `grok-x-trends` — X/Twitter executive activity lookup
- `research-specialist` — deep web research (delegate when search scope is wide)

## Anti-Patterns

- Don't brief on personal blocks, lunches, or recurring standups with no external attendees.
- Don't write briefings for past meetings.
- Don't over-research. A briefing is a quick-look intelligence product, not a biography.
- Don't fabricate executives. If search comes up empty, say so and suggest alternatives.
