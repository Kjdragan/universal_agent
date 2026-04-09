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

### ⏳ Phase 3: MCP Interactive Tool Setup
- [ ] Evaluate and select an MCP (e.g., `netixc/mcp-discord`).
- [ ] Configure it directly with the `DISCORD_BOT_TOKEN` locally.
- [ ] Incorporate into the Universal Agent MCP configuration.
- [ ] Expose Discord context seamlessly to CODIE and ATLAS VP runtimes.

### ⏳ Phase 4: Event Intelligence Pipeline (See: `HANDOFF_05_Discord_Update_Phase4.md`)
- [x] **Investigation Complete:** Decided against capturing audio recordings from stage channels due to high TOS account ban risks and complexity.
- [ ] **GWS Calendar Sync:** Use the `gws` (Google Workspace CLI) natively included in the UA project (e.g., `gws calendar +insert`) instead of a custom MCP to synchronize scheduled events to the user calendar.
- [ ] **Text-Event MVP:** Hook `on_scheduled_event_create` and implement Layer 2 NLP inside `daemon.py` to catch native and chat-based schedules.
- [ ] Notification piping into the C&C `#event-calendar` channel with C&C bot reactions (`✅`, `🎙️`, `📋`, `❌`) to trigger sync and workflows.
- [ ] Post-event digest generation (`event_digest.py`) via the LLM (Sonnet) delivered to the Daily Briefings and LLM Wiki.

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
