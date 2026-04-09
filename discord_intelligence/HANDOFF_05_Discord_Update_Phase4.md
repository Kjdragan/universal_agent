# Discord Intelligence Project: Phase 3 & 4 Status Update

## Overview
The Discord Intelligence architecture consists of two primary services:
1. **Command & Control (C&C) Bot** (`ua-discord-cc-bot.service`): Powered by `DISCORD_BOT_TOKEN`, handles active operations, interactions, and database dispatch polling.
2. **Intelligence Daemon** (`ua-discord-intelligence.service`): Powered by `DISCORD_USER_TOKEN` (via `discord.py-self`), provides passive, read-only ingestion across authorized servers and channels.

Both services are successfully containerized via systemd templates and deployed to production. 

---

## 1. Google Workspace (GWS) CLI Transition (Phase 4 Evolution)
Based on recent architectural decisions, we are bypassing custom MCP or OAuth scopes for Calendar integration. Instead, the Event Intelligence Pipeline will utilize the **Google Workspace (GWS) CLI** that is already natively integrated into the Universal Agent project. 

**Why?**
The `gws` (https://github.com/googleworkspace/cli) natively exposes `gws calendar +insert` and full zero-boilerplate structured JSON outputs for Google Workspace APIs. This mitigates auth complexity and leverages 40+ existing agent skills natively.

**Next Action for GWS:**
The Event Intelligence Pipeline (specifically `event_digest.py` and potentially LLM agent tooling) will be designed to ingest newly discovered scheduled events and wrap the `gws` terminal commands for Calendar synchronization.

---

## 2. Discord User Token Acquisition
The user token is required for the passive intelligence daemon to ingest messages using native Discord API self-bot functionality via `discord.py-self`. 

**Token Status:** 
The token has been successfully extracted from a browser network trace (`/api/v9/science`). 
The authorization header observed was:
* `[REDACTED_DISCORD_USER_TOKEN]`

**Next Action:** 
This token needs to be seeded into our Infisical secrets cluster under the key `DISCORD_USER_TOKEN` across all environments. Once seeded, the `ua-discord-intelligence.service` on the VPS must be restarted to pull the active token and bind the 912 passive channels.

---

## 3. Pipeline Current State & Next Steps

### Completed:
* **Background Database Polling:** The C&C Bot successfully utilizes `discord.ext.tasks` to background poll the `scheduled_events` SQLite loop and dispatch unrecognized events to the `#event-calendar` triage channel.
* **API Event Hook:** `on_scheduled_event_create` and `on_scheduled_event_update` hooks are implemented in `daemon.py` to upsert events formally scheduled within the Discord application directly into our `.db`.
* **CI/CD Stabilization:** Pipeline commits via `stagecommit` and `productioncommit` have successfully executed. Environment and Infisical factories are routing the proper subset of keys.

### Next Steps for AI Planner:
1. **Infisical User Token Upsert:** Upsert the recovered `DISCORD_USER_TOKEN` over Infisical and restart the Production `ua-discord-intelligence.service`.
2. **Phase 3 (MCP Integration):** Finish wiring the `mcp-discord` layer so that coding agents can dynamically query historical channels via Discord's Search API using the bot token, bypassing the latency of full SQLite sweeps.
3. **Phase 4 - Event Pipeline (Text-Based Discovery):** 
    * Implement an NLP layer to listen to generic chat messages within `daemon.py` for text-based scheduling cues (e.g. "Meeting tomorrow at 4pm"). 
    * Route these detected text-events into the `scheduled_events` database, flagged differently than native application scheduled events.
4. **Phase 4 - GWS Calendar Sync:** Wire the C&C bot reactions (`✅`, `🎙️`, `📋`, `❌`) to trigger a background function that calls the `gws calendar` CLI to push approved events to the Google Calendar.
5. **LLM Digest Extraction:** Build out `event_digest.py` to routinely batch parse these ingested messages through Claude/Sonnet to populate the daily briefing artifacts and append knowledge to the NotebookLM Wiki.
