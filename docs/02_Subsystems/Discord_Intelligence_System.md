# Discord Intelligence System

**Canonical source of truth** for the Universal Agent Discord Integration.

**Last Updated:** 2026-04-09

## 1. Overview

The Discord Intelligence subsystem provides passive intelligence gathering and active operational command-and-control for the Universal Agent. It separates listening operations from interactive operations using a dual-token architecture to comply with user-account guidelines and API restrictions.

## 2. Architecture & Components

The system is cleanly decoupled into two persistent services:

### 2.1. Intelligence Daemon (`ua-discord-intelligence.service`)
- **Token Type:** `DISCORD_USER_TOKEN` (User Token via `discord.py-self`)
- **Role:** Passively monitors designated channels without triggering presence alerts or sending outbound messages.
- **Storage:** Stores metadata, content, and signal classifications into a dedicated SQLite hub (`discord_intelligence.db`).
- **Status:** **DEPLOYED (Phase 1)** - Successfully running natively on the production VPS.

### 2.2. Command & Control Bot (`ua-discord-cc-bot.service`)
- **Token Type:** `DISCORD_BOT_TOKEN`
- **Role:** The execution surface. Operates exclusively within the isolated UA Operations Server. It provides slash commands (`/status`, `/task_add`, etc.) and automatically dispatches rich embed alerts from the intelligence DB to specific feed channels (e.g., `#event-calendar`, `#announcements-feed`).
- **Status:** **DEPLOYED (Phase 2)** - Successfully running natively on the production VPS.

## 3. Development Status & Roadmap

The current integration is partially complete based on the 4-phase plan found in `discord_intelligence/PROMPT_Discord_Phases_2_3_4_For_AI_Coder.md`.

### ✅ Phase 1: Passive Intelligence Daemon
- [x] Background service running under user profile.
- [x] Basic signal detection (Layers 1 and 2).
- [x] Secrets loaded dynamically from Infisical on bootstrap.

### ✅ Phase 2: Command & Control Execution
- [x] Operational server scaffolded with `OPERATIONS`, `INTELLIGENCE`, `ARTIFACTS`, and `SYSTEM` categories.
- [x] `cc_bot.py` deployed as a `systemd` unit with isolated token.
- [x] `discord.ext.tasks` background polling architecture verified.

### ✅ Phase 3: MCP Interactive Tool Setup
- [x] **Pivot:** Bypassed `netixc/mcp-discord` because standard bot scopes cannot read historical user-monitored channels. 
- [x] Implemented a custom FastMCP SQLite bridge (`discord_intelligence/mcp_bridge.py`).
- [x] Exposes standard tools (`search_messages`, `get_signals`, `get_events`, `get_insights`) directly linking the Universal Agent strictly to our self-collected intelligence database.

### ✅ Phase 4: Event Intelligence Pipeline
- [x] **Investigation Complete:** Decided against capturing audio recordings from stage channels due to high TOS account ban risks and complexity.
- [x] **GWS Calendar Sync:** Integrated the `gws` (Google Workspace CLI) natively inside `cc_bot.py`. C&C reactions (`✅`, `🎙️`, `❌`) automatically trigger sync to the operator's Google Calendar.
- [x] **Text-Event MVP:** The SQLite database collects and triggers native Discord scheduled events.
- [x] **Event Digest Pipeline:** `event_digest.py` queries messages from the exact event window (+/- 15 mins), dispatches to Sonnet for summary, and saves the intelligence payloads to `digests/` and the knowledge base `kb/briefings/`.

## 4. Operational Runbook

### Managing the Services
Both daemons run locally on the VPS and auto-restart using standard `systemd` mechanics:
```bash
sudo systemctl status ua-discord-intelligence
sudo systemctl status ua-discord-cc-bot
```

### Rotating Secrets
Both components rely entirely on the Infisical secret service. To rotate tokens, do not modify environment files on the VPS:
1. Extract the new Token (Bot Token from the Developer Portal, or User Token from the web UI Network tab).
2. Use the Infisical CLI script to upsert to staging and production:
   ```bash
   uv run scripts/infisical_upsert_secret.py --environment production --secret "DISCORD_USER_TOKEN=YOUR_NEW_TOKEN"
   ```
3. Restart the specific systemd service on the VPS to trigger `infisical_loader.py` to fetch the updated payload.
