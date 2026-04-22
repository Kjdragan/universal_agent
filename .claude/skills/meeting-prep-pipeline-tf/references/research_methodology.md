# Executive Research Methodology

## Search Strategy for Each Executive

### Step 1: Identity Verification
- Search query: `"[Company]" "[Product Area]" executive VP Director`
- Verify against at least 2 independent sources (company website, LinkedIn, press releases)
- Acceptable verification sources: company official site, TechCrunch, Crunchbase, LinkedIn
- Unacceptable: forum posts, unattributed blog articles, AI-generated directories

### Step 2: Activity Intelligence
- Use `x_trends_posts` with query: `[Executive Name] [Company]` to find recent commentary
- Search for: conference talks (YouTube), blog posts, podcast appearances, product announcements
- Prioritize activity from the last 90 days. Anything older than 6 months is stale context.

### Step 3: Relevance Scoring
For each identified executive, score relevance to the meeting purpose:
- **High**: Directly manages the product area of interest
- **Medium**: Adjacent role (engineering lead for related team, strategy, partnerships)
- **Low**: Senior but in an unrelated division

Only schedule meetings with High-relevance executives. Briefings cover all attendees.

## Time Budget
- Research per executive: 2-3 minutes maximum
- Total research phase: 15 minutes for up to 6 executives
- Briefing generation per meeting: 3-5 minutes

## Scope Definitions

### "Upcoming meeting"
- Any calendar event from the current time through end of day Friday of this week
- Must have at least one non-self attendee OR a title suggesting an external meeting
- Exclude: personal blocks, lunch, gym, commuting, recurring daily standups with no external attendees

### "Executive"
- Titles: VP, SVP, C-suite, Head of, Director, GM, or equivalent
- Must have publicly verifiable role at the named company
- If no one at this level can be found, document what was searched and report the gap

### "Briefing-worthy event"
- Any upcoming meeting with at least one external attendee, OR
- Any meeting explicitly requested by the user, OR
- Any meeting with a C-suite or VP-level attendee
