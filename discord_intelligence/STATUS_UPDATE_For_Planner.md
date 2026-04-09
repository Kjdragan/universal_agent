# Discord Intelligence System: Status Update for AI Planner

**Date:** 2026-04-09
**Branch context:** All changes currently pushed to `develop` and `main` (Production)

This document provides a current state assessment of the Universal Agent Discord Integration based on the Phase 2, 3, & 4 roadmap. It is structured to help an AI Planner immediately understand what is finished and what needs to be planned next.

---

## 1. What We Just Completed (Phases 1 & 2)

We successfully established the dual-token architecture for the Discord subsystem, running as two decoupled `systemd` background services on the production VPS.

### A. Intelligence Daemon (Phase 1)
- **Component:** `daemon.py` (`ua-discord-intelligence.service`)
- **Token:** `DISCORD_USER_TOKEN` (User Token loaded via Infisical)
- **Status:** **COMPLETE & DEPLOYED**.
- **Capabilities:** It actively monitors channels under the user's account passively (discord.py-self), saves messages to the local `discord_intelligence.db`, performs deterministic Layer 2 signal detection, and pipes Layer 3 analysis to the local Task Hub / AgentMail.

### B. Command & Control Server (Phase 2)
- **Component:** `cc_bot.py` (`ua-discord-cc-bot.service`)
- **Token:** `DISCORD_BOT_TOKEN` (Bot Token loaded via Infisical)
- **Status:** **COMPLETE & DEPLOYED**.
- **Capabilities:**
  - Setup script (`cc_setup.py`) successfully constructed the operational channel topology (e.g., `#event-calendar`, `#announcements-feed`, `#alerts`, `#simone-chat`) in the owner's Discord server.
  - Implements a background loop (`tasks.loop`) to poll SQLite database updates (queued events/signals) and dispatch them as rich embeds to the target channels.
  - Registers interactive slash commands (e.g., `/status`, `/task add`) for operational observability directly from Discord.

### C. Infrastructure & CI/CD
- Fixed `.venv` permission issues via CI/CD templates (`install_vps_systemd_units.sh`).
- Tokens are centrally managed by Infisical (`uv run scripts/infisical_upsert_secret.py`). There is NO `.env` file containing these tokens in the repo.
- Canonical codebase documentation updated in `docs/02_Subsystems/Discord_Intelligence_System.md`.

---

## 2. What Needs to be Planned Next (Phases 3 & 4)

The planner should focus its efforts on the remaining tasks from the original prompt.

### Task A: Phase 3 — MCP Tool Setup
**Goal:** Enable our VP agents (ATLAS, CODIE) and local IDE to interactively query Discord channels.
**Remaining Work:**
1. Evaluate and install `netixc/mcp-discord` (or equivalent Node.js alternative).
2. Wire it up with the `DISCORD_BOT_TOKEN` in `~/.mcp.json`.
3. Test tool accessibility for reading channel metadata, listing categories, and querying recent messages across our servers.
4. Update `discord_intelligence/HANDOFF_04_Discord_MCP_Tool_Setup.md`.

### Task B: Phase 4 — Event Intelligence Pipeline 
**Goal:** Automate live event discovery (Scheduled Events or text announcements) and post-event LLM digestion.
**Remaining Work:**
1. **Investigation:** Answer technical unknowns in `discord_intelligence/EVENT_PIPELINE_INVESTIGATION.md`:
   - Can `discord.py-self` safely receive/record audio from stage channels?
   - What are the TOS implications vs. reality for recording public stages?
   - How should we integrate the Google Calendar API for automated scheduling?
2. **Text-Based MVP Build:**
   - Extend the daemon to capture `on_scheduled_event_create` and text mentions specifying event times.
   - Send event cards to `#event-calendar` with RSVP reactions (✅, 🎙️, 📋, ❌).
   - Build the post-event text digest workflow: automatically gather chat context from the event window, digest with an LLM, and push summary to the `latest_briefing.md` and LLM Wiki.

## Planner Directives

1. **Start with Phase 3 (MCP Setup)** as it is an immediate force multiplier for other agent tooling and requires standard configurations.
2. **Before touching code for Phase 4**, run the required investigation block to determine the boundaries of the `discord.py-self` library for audio streams. Do not guess; write a focused test script using our dependency standards first.
