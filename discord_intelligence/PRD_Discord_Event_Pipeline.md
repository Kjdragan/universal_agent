# PRD: Discord Event Intelligence Pipeline

**Parent Document:** `Discord_UA_Master_Plan.md`
**Priority:** Phase 4 (after Intelligence Daemon and Command & Control are stable)
**Type:** Product Requirements Document — investigate, design, then hand off for development
**Status:** Vision & Requirements (needs technical investigation before implementation)

---

## Executive Summary

Build an autonomous pipeline that discovers, tracks, records, digests, and briefs the owner on Discord community events (talks, AMAs, product launches, workshops, office hours) happening across all monitored AI Discord servers. The owner goes from **missing almost every event** to receiving pure-signal daily briefings with key takeaways, knowledge base entries, and links to recordings — without having to attend a single session personally.

This is one of the highest-value features in the entire Discord integration because it converts ephemeral, time-bound community knowledge into persistent, searchable intelligence.

---

## The Problem

The owner is a member of many AI Discord servers (Anthropic, Google/Gemini, OpenAI, HuggingFace, LangChain, etc.). These servers regularly host:

- **Scheduled talks and presentations** — Product teams presenting new features, researchers sharing findings
- **AMAs (Ask Me Anything)** — Developers and company leaders answering community questions
- **Office hours** — API support sessions, debugging walkthroughs
- **Launch events** — New model releases, SDK launches, feature announcements
- **Workshops** — Hands-on tutorials, integration guides
- **Community discussions** — Themed discussion events on specific topics

**Current state:** The owner misses these events constantly because:
1. They don't know the events exist (no discovery mechanism)
2. Even when aware, scheduling conflicts prevent attendance
3. There's no way to "catch up" on what happened at a missed event
4. The insights from these events evaporate — they're locked in ephemeral voice/stage channels

## The Solution: A Four-Stage Autonomous Pipeline

### Stage 1: Event Discovery

**Trigger:** Discord Scheduled Events API + text-based event detection in monitored channels

**How it works:**
- The Intelligence Daemon (HANDOFF_02) already monitors the Discord Scheduled Events API via `on_scheduled_event_create` and `on_scheduled_event_update` events
- Additionally, Layer 2 signal detection catches text-based event announcements in channels (messages mentioning events with time/date indicators)
- Both sources feed into the `scheduled_events` table in the intelligence database

**Output:** A structured event record with:
- Event name, description, server, time, type (stage/voice/external)
- Auto-categorization: talk, AMA, office hours, launch, workshop, community discussion
- Relevance score based on: server priority, topic match to owner's interests, event type

**Deterministic (zero LLM cost):** Event discovery is entirely API-driven and pattern-matched.

### Stage 2: Event Triage & Calendar Integration

**Trigger:** New event discovered in Stage 1

**How it works:**
- Events above a relevance threshold get **auto-posted to the owner's `#event-calendar` channel** in the CC Discord server (from HANDOFF_03)
- Events are formatted as rich embeds with: server name, event title, description, time (converted to owner's timezone CST/CDT), event type, and a relevance indicator
- **Calendar integration**: Events above a higher threshold get automatically added to the owner's Google Calendar as tentative entries. The owner can accept, decline, or modify from their calendar app.
- The owner can react to event posts in Discord:
  - ✅ = "I want to attend this"
  - 🎙️ = "Record this for me"
  - 📋 = "Just give me a summary afterward"
  - ❌ = "Not interested"

**Owner feedback loop:** Reactions teach the system which types of events the owner values. Over time, relevance scoring improves.

**LLM cost:** Minimal. One LLM call per event for relevance scoring and categorization. With a cheap model, this costs fractions of a cent per event.

### Stage 3: Event Recording & Capture

**Trigger:** Event starts AND owner has marked it for recording (🎙️) OR it's above the auto-record threshold

**This stage requires technical investigation. Key questions:**

1. **Can a Discord bot record stage channel audio?**
   - Discord bots CAN join voice/stage channels using `discord.py[voice]`
   - They CAN receive audio streams (Opus codec)
   - Recording requires: `pip install discord.py[voice]`, plus `ffmpeg` on the VPS
   - **Legal/TOS consideration:** Discord's TOS and many server rules restrict recording without consent. Recording in public stage events where the speakers are broadcasting publicly is generally more acceptable than recording private voice chats. **This needs careful evaluation per-server.**

2. **Alternative: Text-based capture for text events**
   - Many "events" happen in text channels (AMAs, Q&As, announcements)
   - These are already captured by Layer 1 ingestion
   - No recording needed — just identify the event-related messages and group them

3. **Alternative: Third-party recording services**
   - Some Discord bots/services specialize in recording
   - The UA could trigger an external recording service via API

4. **Alternative: Post-event scraping**
   - Stage channels often have a companion text channel where notes/links are shared
   - Voice events often generate recap posts from moderators
   - Capture these text artifacts even if audio recording isn't feasible

**Recommended approach (to be validated during technical investigation):**
- **Text-based events**: Already captured by Layer 1. Group messages by event timeframe.
- **Voice/stage events**: Attempt bot-based audio recording where permissible. Fall back to capturing associated text channels and any posted recaps.
- **External events** (linked to external platforms like YouTube, Twitter Spaces): Detect and log the external URL. Potentially use the UA's existing YouTube pipeline for YouTube-hosted events.

### Stage 4: Event Digestion & Knowledge Base Integration

**Trigger:** Event ends or recording is complete

**How it works:**

For **text-based events** (AMAs, text Q&As):
```
Event messages (from Layer 1 DB)
    │
    ▼
LLM Processing (medium model — needs good summarization)
    │
    ├── Key Takeaways (3-5 bullet points)
    ├── Notable Quotes/Insights (attributed to speakers)
    ├── Action Items (things the owner should know/do)
    ├── New Tools/Resources Mentioned (with links)
    ├── Questions & Answers Summary
    │
    ▼
Artifacts Generated:
    ├── Briefing artifact (markdown, stored in UA artifacts)
    ├── LLM Wiki entry (pushed to external knowledge vault)
    └── Task Hub items (if action items are detected)
```

For **audio recordings** (if feasible):
```
Audio recording (from Stage 3)
    │
    ▼
Speech-to-Text (Whisper via UA's existing audio-to-text skill)
    │
    ▼
Transcript
    │
    ▼
LLM Processing (same as text-based events above)
    │
    ▼
Artifacts Generated:
    ├── Full transcript (stored, searchable)
    ├── Briefing artifact with timestamps
    ├── LLM Wiki entry
    ├── Audio file preserved (linked in briefing)
    └── Task Hub items
```

**LLM cost:** This is the most expensive stage. A 1-hour event transcript might be 15-20K tokens to process. Using a mid-tier model (Claude Sonnet via ZAI): ~$0.05-0.15 per event. At maybe 5-10 events per week across all servers, this is $1-6/month. Still negligible.

### Output: Daily Intelligence Briefing

The pipeline culminates in a section of the owner's daily briefing:

```
## 📅 Discord Events Intelligence

### Yesterday's Events (3 processed)

🎤 **Anthropic: Claude Agent SDK Deep Dive** (Stage Talk, 45 min)
Server: Anthropic | Attended: No | Recorded: Yes
Key Takeaways:
- Agent SDK v2 introduces persistent tool state across turns
- New hook system replaces the old middleware pattern
- Breaking change: AgentSetup constructor signature changed
→ Full digest: [Knowledge Base Link]
→ Recording: [Audio File Link]
→ ⚡ Action: Review breaking changes against our UA's hook implementation

🗣️ **LangChain: Office Hours #47** (Text Q&A, 2 hours)
Server: LangChain | Attended: No | Text captured: Yes
Key Takeaways:
- LangGraph 0.3 shipping next week with improved state persistence
- Community workaround for the memory leak in recursive agent chains
- New integration with Discord (relevant to our project!)
→ Full digest: [Knowledge Base Link]

📢 **Google Gemini Labs: Gemini 2.5 Launch Event** (Stage + Text, 1.5 hours)
Server: Google AI | Attended: No | Recorded: Yes
Key Takeaways:
- Gemini 2.5 Pro available today in AI Studio
- 2M token context window now standard
- New "thinking" mode similar to Claude's extended thinking
→ Full digest: [Knowledge Base Link]
→ Recording: [Audio File Link]

### Upcoming Events (Next 7 Days)

📅 Tomorrow 2:00 PM CDT — **Anthropic: MCP Working Group** (Anthropic Discord)
📅 Thursday 11:00 AM CDT — **HuggingFace: Smolagents Workshop** (HuggingFace Discord)
📅 Saturday 10:00 AM CDT — **OpenAI: Agents SDK Community Call** (OpenAI Discord)
```

---

## Technical Requirements

### Must Have (MVP)
- [ ] Event discovery via Discord Scheduled Events API (already in HANDOFF_02)
- [ ] Event posting to `#event-calendar` channel with reaction-based owner input
- [ ] Text-based event capture (grouping Layer 1 messages by event timeframe)
- [ ] LLM-powered event digest generation
- [ ] Digest integration into daily briefing pipeline
- [ ] LLM Wiki push for high-value event digests
- [ ] Upcoming events section in daily briefing

### Should Have
- [ ] Google Calendar integration (auto-create tentative entries)
- [ ] Owner reaction feedback loop (learning which events matter)
- [ ] Task Hub item creation for detected action items
- [ ] Cross-event trend detection ("Anthropic and LangChain both mentioned the same new pattern")

### Could Have (Ambitious)
- [ ] Voice/stage channel audio recording (requires TOS investigation)
- [ ] Speech-to-text processing of recordings (UA has audio-to-text skill)
- [ ] Audio file preservation and linking in briefings
- [ ] Auto-detection of event-adjacent content (pre-event discussions, post-event recaps)
- [ ] Event quality scoring (did this event produce useful signal? Learn for future prioritization)

### Won't Have (Out of Scope for Now)
- [ ] Live-streaming events to the owner
- [ ] Real-time transcription during events
- [ ] Bot participation in events (asking questions on behalf of owner)

---

## Integration Points

| UA System | Integration |
|-----------|------------|
| Intelligence Daemon (HANDOFF_02) | Event discovery, text capture, Layer 2 signals |
| Command & Control (HANDOFF_03) | `#event-calendar` channel, reaction-based input |
| Proactive Pipeline | Daily briefing section, autonomous digest generation |
| LLM Wiki | Event digests pushed to external knowledge vault |
| Task Hub | Action items from events become tasks |
| VP Agents (ATLAS) | Digest generation delegated as research missions |
| Audio-to-Text Skill | Transcription of recorded audio (if implemented) |
| Google Calendar | Tentative event entries (if integrated) |

---

## Open Questions for Technical Investigation

1. **Discord voice recording feasibility**: What are the actual technical capabilities and TOS constraints for bot-based audio recording in stage/voice channels?
2. **Google Calendar API integration**: Does the UA currently have Google Calendar access? What's the effort to add it?
3. **Event duration estimation**: Discord Scheduled Events API provides start/end times, but how accurate are they? Do we need heuristics for detecting when events actually end?
4. **Cross-server event deduplication**: Some events are announced in multiple servers (e.g., a tool launch might be discussed in both the tool's server and a general AI server). How do we avoid duplicate processing?
5. **Recording storage**: Where do audio recordings live? VPS disk? Cloud storage? How long do we retain them?

---

## Value Proposition

**Before this pipeline:** The owner misses 90%+ of community events. The 10% attended are consumed in real-time with no persistent artifacts. Knowledge evaporates.

**After this pipeline:** Every relevant event across every monitored Discord server is automatically discovered, tracked, captured, digested, and surfaced as pure signal in daily briefings. The owner gets the equivalent of a full-time research assistant attending every event, taking perfect notes, and delivering concise briefings every morning.

**Estimated cost:** $5-15/month in LLM processing (cheap model for discovery, mid-tier for digests). Zero human time for event monitoring. Owner time reduced to reviewing briefings and swiping left/right on recommendations.

---

## Implementation Notes

1. **This PRD should be investigated further before coding begins.** The technical investigation for voice recording feasibility and Google Calendar integration should happen first.
2. **The MVP (text-based event processing) can be built as soon as HANDOFF_02 and HANDOFF_03 are stable.** Voice recording is a "could have" enhancement.
3. **Delegate the investigation**: This is an excellent candidate for an ATLAS research mission — investigating Discord recording capabilities, TOS constraints, and calendar API integration options.
4. **The owner explicitly called this a "superpower" feature.** It should be designed and implemented with care. Don't rush it — the design-first approach applies especially here.
5. **Preserve all detail from this PRD in any implementation plans.** The owner specifically requested that detail not be lost to summarization.
