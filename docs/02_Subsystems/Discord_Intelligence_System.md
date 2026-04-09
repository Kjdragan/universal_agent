# Discord Intelligence System

**Canonical source of truth** for the Universal Agent Discord Integration.

**Last Updated:** 2026-04-09

## 1. Overview

The Discord Intelligence subsystem provides passive intelligence gathering and active operational command-and-control for the Universal Agent. It separates listening operations from interactive operations using a dual-token architecture.

### Architectural Decision: Bot API vs. User Token Extractor
Early designs attempted to use standard Discord Bot capabilities (e.g., `netixc/mcp-discord`) for full intelligence gathering. This approach failed because traditional Discord bots **cannot read historical messages or passively ingest data across hundreds of private servers** unless the bot is specifically invited with administrative scopes.

To bypass these API restrictions safely without being flagged as an automated spam client:
1. **The extraction layer** utilizes `discord.py-self` with a **User Token** to passively mirror read-only channels acting completely un-interactive.
2. **The MCP interface** bypasses Discord infrastructure entirely by connecting the Universal Agent *directly* to the local SQLite database populated by the user token.
3. **The command surface** is strictly delegated to a standard **Bot Token** restricted entirely to a private Ops Server.

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
- [x] Implemented a custom FastMCP SQLite bridge (`discord_intelligence/mcp_bridge.py`) that strictly links the Universal Agent to our local intelligence database.
- [x] **Task Hub Integration:** Adjusted the deployment code to correctly use the durable database connection (`connect_runtime_db(get_activity_db_path())`) to support dynamic task assignments from incoming Discord intelligence.

### ✅ Phase 4: Event Intelligence Pipeline
- [x] **Investigation Complete:** Decided against capturing audio recordings from stage channels due to high TOS account ban risks and complexity.
- [x] **GWS Calendar Sync:** Integrated the Google Workspace CLI via `npx @googleworkspace/cli calendar +insert` natively inside `cc_bot.py`.
    - **Auth Discovery:** The CLI must first be authenticated locally with `npx @googleworkspace/cli auth login` to handle OAuth consent and cache credentials in the secure keyring.
    - **Payload Discovery:** The Calendar API requires strict ISO 8601 formatting for `--start` and `--end` timestamps rather than natural language parsing.
    - C&C reactions (`✅`, `🎙️`, `❌`) on Discord event alerts automatically trigger this sync to the operator's Google Calendar using those parameters.
- [x] **Text-Event MVP:** The SQLite database collects and triggers native Discord scheduled events.
- [x] **Event Digest Pipeline:** `event_digest.py` queries messages from the exact event window (+/- 15 mins), dispatches to Sonnet for summary, and saves the intelligence payloads to `digests/` and the knowledge base `kb/briefings/`.
- [x] **Database Hardening:** Plugged SQLite descriptor leaks across the daemon and C&C bot feeds by strictly wrapping `_get_conn` as a python Context Manager.
- [x] **Reliable Upstream Task Dispatch:** Discord intelligence tasks now unpack root-level `title` and `description` properties and default to `agent_ready: 1`, solving the `-2.0` scoring penalty during central Task Hub queue insertion.
- [x] **LLM Proxy Optimization:** Hardcoded `config.yaml` model endpoints to directly utilize explicit Z.AI emulation proxies (`glm-4.5-air` for triage parsing and `glm-5-turbo` for deep insight extraction).

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
