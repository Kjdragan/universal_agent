---
name: discord_intelligence_system
description: Discord Intelligence subsystem deployed on VPS — 24/7 monitoring of AI community Discord servers with triage and C&C bot capabilities
type: project
---

# Discord Intelligence System

**Deployed:** 2026-04-09 (briefed by Kevin)
**Status:** Live, running 24/7 on VPS

## Architecture

### 1. Intelligence Daemon
- Monitors **912 channels** across **28 Discord servers**
- Key servers: Claude/Anthropic, Google Labs, Google Gemini, Google Developer Community, Hugging Face, NotebookLM, Friends of the Crustacean (OpenClaw), Z.ai, Crawl4AI, AgentMail, ComposioHQ, Jina AI, Mem0, Unsloth AI, Railway, Docker, Supabase
- Three processing layers:
  - **Layer 1**: Capture everything (zero cost, pure storage)
  - **Layer 2**: Deterministic signal detection (announcements, new releases, scheduled events) with immediate alerts
  - **Layer 3**: Periodic LLM-powered triage (cheap model) to identify the most valuable community insights

### 2. Command & Control Bot
- Operational hub: kdragan's Discord server
- Structured channels: #announcements-feed, #research-feed, #event-calendar, #alerts, #simone-chat, #mission-status, #briefings
- Serves slash commands and posts intelligence feeds

## How Simone Should Use This

- **Morning briefings**: Incorporate Discord Intelligence section with significant signals (new releases, tool updates, community insights)
- **Proactive opportunity identification**: Watch for signals relevant to ClearSpring CG monetization, freelance automation, and UA capability improvements
- **Competitive intelligence**: Track what other AI agent builders and tool vendors are shipping
- **Event awareness**: Stay on top of scheduled events, AMAs, launches from monitored communities

## Data Access
- Local database on VPS stores all captured messages
- Layer 2 signals can alert immediately
- Layer 3 triage identifies high-value insights from community discussions
