# Discord × Universal Agent Integration — Master Plan

## Document Navigation

This is the **master document** for the Discord integration project. It captures the overall vision, key decisions, architectural approach, and links to all detailed handoff documents. Return to this document whenever you need to re-orient after completing individual workstreams.

### Related Documents (in recommended reading order)

1. **This document** — Master Plan (overall vision, decisions, phasing)
2. **`HANDOFF_01_Channel_Inventory_Utility.md`** — First deliverable: a bot utility that inventories all Discord servers/channels the user belongs to, producing a structured catalog for planning
3. **`HANDOFF_02_Discord_Intelligence_Daemon.md`** — Phase 1 core: the persistent monitoring daemon (Layers 1-3), database schema, integration with UA proactive pipeline
4. **`HANDOFF_03_Discord_Command_Control.md`** — Phase 2: the user's own Discord server as a command & control interface with slash commands mapped to UA capabilities
5. **`HANDOFF_04_Discord_MCP_Tool_Setup.md`** — Standalone MCP tool setup for immediate use by agents and developers
6. **`PRD_Discord_Event_Pipeline.md`** — Product Requirements Document for the autonomous event discovery, recording, digestion, and briefing pipeline (the "superpower" feature)

---

## Project Context

### What is the Universal Agent (UA)?

The UA is a bespoke AI agent system built primarily in Python, deployed on a VPS. Production repository: **https://github.com/Kjdragan/universal_agent**

Key components relevant to Discord integration:
- **Simone** — Executive orchestrator agent; triages tasks, delegates to VP agents, communicates with the owner via email/AgentMail
- **VP Agents (CODIE & ATLAS)** — Worker agents; CODIE handles coding/technical, ATLAS handles research/analysis
- **Task Hub** — Kanban-style task lifecycle with scoring, delegation, execution tracking
- **Proactive Pipeline** — Autonomous task execution: email triage, Task Hub scoring, ToDo execution, morning briefings, delegation lifecycle
- **CSI (Creator Signal Intelligence)** — Signal intelligence subsystem for external source ingestion (YouTube currently). Has its own FastAPI runtime, SQLite event DB, systemd timer fleet. Operates as a logically separate subsystem within the same repo. **Note: Discord integration will be built as a NEW clean subsystem, not extending CSI.** The CSI pattern is informative but the Discord system should not inherit CSI's codebase or limitations.
- **LLM Wiki System** — External knowledge vault and internal memory vault for persistent knowledge management
- **Heartbeat Service** — Health monitoring across the system
- **Memory System** — Tiered memory with auto-flush and lossless DAG-based compression
- **ZAI Proxy** — Anthropic API proxy for LLM inference; the owner has generous token limits including access to cheap models (e.g., Claude 4.5 Haiku) with high concurrency
- **Infrastructure** — VPS (cloud), Infisical secrets, Tailscale networking, rotating residential proxy (available but hopefully not needed for Discord)

### Current Communication Channels
- **Email/AgentMail with Simone** — Primary channel currently; owner enjoys direct conversational interaction
- **Telegram bot** — Exists but underutilized; owner is open to replacing it if Discord proves richer
- **Web dashboard** — Task Hub, chat panel, activity log
- **Discord** — Owner has an existing Discord server (currently empty/unused) and is a member of many AI-focused Discord servers

---

## Vision Statement

Discord becomes a **first-class intelligence source and operational channel** for the Universal Agent system, enabling:

1. **Persistent intelligent monitoring** of AI research communities — extracting signal from noise across dozens of Discord servers, feeding curated intelligence into the UA's knowledge base and proactive pipeline
2. **Command & control** — the owner's Discord server becomes a structured interface for directing and observing UA operations, richer than Telegram or email for certain workflows
3. **Autonomous agent research** — VP agents can query Discord as a tool during mission execution, using MCP integration
4. **Event intelligence pipeline** — Automated discovery, recording, digestion, and briefing of Discord community events (talks, AMAs, product launches)

### Core Philosophy

The owner's stated design principles for this integration:

> "There is no wasted compute by those agents doing all this stuff. Especially when I have a standalone VPS. There's 24 hours in the day... my agents could be generating work for me. My ability to review a pipeline of projects that they have created on their own and just swiping left or right metaphorically is not a waste of time for the agents and it potentially adds significant value for me."

> "As they do more of this work and I guide them on what I like, they will be better understanding my needs, desires and workflows. So they will get even better at proactive activity."

**Design intent from the beginning:** All features should be designed with proactive autonomous agent activity as the backdrop. Every channel integration should be able to spur independent activity by agents without human direction. The system should be designed upfront for this, not retrofitted later.

---

## Key Architectural Decisions (Finalized)

### Decision 1: Build Fresh, Not on CSI
The Discord intelligence system will be a **new, clean subsystem** following the CSI architectural pattern (source ingestion → event database → scoring/triage → surface insights → generate artifacts) but with its own codebase. This avoids inheriting CSI's limitations while learning from its design.

### Decision 2: Hybrid Daemon + MCP Approach
The system will use **both**:
- A **persistent monitoring daemon** (discord.py bot) running 24/7 on the VPS for passive ingestion and deterministic alerting
- **Discord MCP tools** for on-demand agent queries during mission execution

These serve different needs: the daemon ensures nothing is missed and builds the knowledge base over time; the MCP tools give agents interactive query capability.

### Decision 3: Three-Layer Intelligence Architecture
Cost is controlled by separating processing into three layers:

| Layer | What It Does | LLM Cost | Runs When |
|-------|-------------|----------|-----------|
| **Layer 1: Ingestion** | Capture all messages from monitored channels into SQLite | Zero | 24/7 real-time |
| **Layer 2: Deterministic Signals** | Regex/keyword detection for announcements, releases, events; Discord Scheduled Events API monitoring | Zero | 24/7 real-time |
| **Layer 3: LLM Triage** | Batch processing of accumulated messages for relevance scoring, summarization, insight extraction | Low (cheap model) | Scheduled (configurable) |

### Decision 4: Err on Capturing More, Not Less
Given that the monitoring daemon is lightweight (50-100MB RAM, negligible CPU) and storage is cheap, the system should **collect aggressively and filter intelligently**. Even Tier C community chat channels should be captured because:
- Expert users surface valuable signals (bug reports, workarounds, integration tips)
- The noise can be filtered cheaply using high-concurrency cheap models (e.g., Claude 4.5 Haiku via ZAI)
- Better to have data you don't need than to miss data you can't recover

### Decision 5: Cheap Model Strategy for Community Chat Processing
Community chat (Tier C) processing should use a **cheap, fast model with high concurrency** (e.g., Claude 4.5 Haiku or equivalent via ZAI plan). This avoids consuming concurrency slots on premium models used for coding work. The triage task doesn't require advanced reasoning — it needs fast, cheap, concurrent scanning to identify the ~5% of community messages that contain genuine signal.

### Decision 6: Multiple Channels, Survival of the Fittest
Discord will be built alongside existing channels (email/AgentMail, Telegram, web dashboard), not replacing them. The owner is comfortable with overlapping channels and will let natural workflow preferences determine which channels thrive. Discord may eventually replace Telegram if it proves richer.

### Decision 7: Owner's Discord Server is Fully Available
The owner's existing Discord server has no external users or obligations. It exists solely to serve the UA system's needs. Agents can use it freely for any operational purpose.

---

## Phasing Plan

### Pre-Phase: Channel Inventory (Do First)
**Deliverable:** `HANDOFF_01_Channel_Inventory_Utility.md`

Before building the monitoring daemon, we need to know what we're monitoring. Build a small utility bot that authenticates with the owner's Discord account (via a bot token), enumerates all servers and channels, and produces a structured catalog. The owner then classifies channels into tiers (A/B/C) and priority levels.

**Why first:** The channel inventory informs the monitoring daemon's configuration and helps prioritize which channels to focus on. It also reveals the scope of the project (how many servers, how many channels, what types).

### Phase 1: Discord Intelligence Daemon
**Deliverable:** `HANDOFF_02_Discord_Intelligence_Daemon.md`

The core monitoring system. A discord.py bot running on the VPS that:
- Connects to all monitored servers via the Discord Gateway
- Captures messages from configured channels (Layer 1)
- Detects deterministic signals — announcements, releases, scheduled events (Layer 2)
- Routes alerts to Simone via existing UA communication pathways
- Stores everything in a dedicated SQLite database
- Includes a scheduled batch process for Layer 3 LLM triage
- Integrates with the UA proactive pipeline (morning briefings, mission triggers)

### Phase 2: Command & Control Server
**Deliverable:** `HANDOFF_03_Discord_Command_Control.md`

The owner's Discord server becomes an operational interface:
- Structured channels for different concerns (missions, alerts, research feed, artifacts, etc.)
- Slash commands mapped to UA capabilities (task creation, briefings, status checks, research requests, wiki queries)
- Rich embed responses with status indicators and artifact links
- Thread-based mission tracking
- Integration with Tier 1 (research feed channel populated by intelligence daemon)

### Phase 3: MCP Tool Integration
**Deliverable:** `HANDOFF_04_Discord_MCP_Tool_Setup.md`

Discord becomes a queryable tool in the agent toolkit:
- Set up netixc/mcp-discord (Python-native) or IQAIcom/mcp-discord
- Add to .mcp.json configuration
- VP agents gain ability to search/read Discord channels during mission execution
- Simone can commission "research Discord for X" as a delegatable task

### Phase 4: Event Intelligence Pipeline
**Deliverable:** `PRD_Discord_Event_Pipeline.md`

The autonomous event discovery and digestion system:
- Monitor Discord Scheduled Events API across all servers
- Detect community events (talks, AMAs, launches, workshops)
- Auto-create calendar entries for the owner to review
- For approved/interesting events: attempt to record sessions
- Post-event: digest recordings, extract key signals, generate briefing artifacts
- Surface in daily briefings with knowledge base links and audio files

---

## Channel Monitoring Taxonomy

### Tier A — Announcement Channels (Highest Signal)
Channels like `#announcements`, `#releases`, `#changelog`, `#news` in major AI servers. Low volume, almost every message is valuable. **Processing: Layer 2 deterministic alerting — every message flagged to Simone immediately.**

### Tier B — Technical Discussion Channels (Medium Signal)
Channels like `#api-help`, `#dev-support`, `#general-dev`, `#showcase`. Higher volume, rich with practical information. **Processing: Layer 1 capture + Layer 3 batch triage, with mission-aware relevance scoring.**

### Tier C — Community Chat (Lower Signal, High Potential Value)
General discussion channels. High noise but occasionally surfaces expert insights, bug reports, workarounds, novel approaches. **Processing: Layer 1 full capture + Layer 3 cheap-model scanning (Claude 4.5 Haiku) focused on identifying expert signals, novel solutions, and actionable intelligence.**

Key insight: Tier C is NOT "ignore" tier. Real experts discussing real problems in these channels produce some of the most actionable intelligence. The strategy is aggressive capture + cheap intelligent filtering, not exclusion.

### Special: Event Channels
Any channel or API surface where scheduled events appear. **Processing: Layer 2 deterministic capture via Discord Scheduled Events API + calendar integration.**

---

## Technical Notes

### Discord API Key Facts
- **MESSAGE_CONTENT privileged intent**: Required to read message text. For bots in <100 servers (our case), just toggle ON in Developer Portal. No approval process needed.
- **Rate limits**: 50 requests/second globally per bot. More than sufficient for passive monitoring.
- **Gateway connection**: Real-time WebSocket push from Discord. No polling needed. Extremely lightweight.
- **Scheduled Events API**: Native Discord feature. Events fire `on_scheduled_event_create` and `on_scheduled_event_update`. Zero-cost deterministic monitoring.
- **No proxy needed**: Discord API is standard authenticated WebSocket, not web scraping. Residential proxy not required.
- **discord.py v2.7+**: Actively maintained Python library. Async, full API coverage, well-documented.

### Resource Profile (VPS Impact)
- **RAM**: ~50-100MB for the bot process
- **CPU**: Negligible (async event-driven, not polling)
- **Storage**: SQLite database grows with message volume; even millions of messages are manageable
- **Network**: Single persistent WebSocket connection to Discord Gateway
- **LLM cost**: Zero for Layers 1-2; controlled/scheduled for Layer 3 using cheap models

### Existing Discord MCP Servers (Evaluated)
1. **netixc/mcp-discord** (Python) — Best fit for UA's Python stack. Tools: get_server_info, list_members, read/send messages.
2. **IQAIcom/mcp-discord** (Node.js) — Most comprehensive feature set. Full channel/forum/webhook management.
3. **SaseQ/discord-mcp** (Java/Docker) — Docker-packaged, easy deployment.

**Recommendation:** Start with netixc/mcp-discord for Python compatibility. Consider IQAIcom/mcp-discord if richer features are needed later.

---

## Open Items / Requires Owner Input

1. **Channel inventory**: Owner needs to run the inventory utility and classify channels into tiers
2. **Priority servers**: Which AI Discord servers are highest priority for initial monitoring?
3. **Discord bot token**: Owner needs to create a Discord application and bot in the Developer Portal
4. **Notification preferences**: How should Simone alert the owner about Discord signals? (Email, Discord DM, Task Hub item, all of the above?)
5. **Event recording capability**: Technical investigation needed — can Discord stage/voice events be recorded by a bot? What are the legal/TOS considerations?
6. **Channel structure for owner's server**: Finalize the channel layout for command & control

---

## Document Maintenance

This master document should be updated as phases are completed, decisions change, or new requirements emerge. Each handoff document is self-contained but references this master plan for context.

**Last updated:** April 8, 2026
**Status:** Planning / Pre-Phase (Channel Inventory)
