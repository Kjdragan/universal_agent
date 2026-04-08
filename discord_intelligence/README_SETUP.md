# Discord Intelligence — Setup & Execution Guide

## For the Owner (Kevin)

### What's in this package

This directory contains everything needed to build the Discord Intelligence subsystem for Universal Agent. It goes in your repo at `discord_intelligence/` (same level as `src/`, `docs/`, `CSI_Ingester/`, etc.)

### Files

```
discord_intelligence/
├── README_SETUP.md                              ← YOU ARE HERE
├── __init__.py                                  ← Package init
├── requirements.txt                             ← Python dependencies
├── Discord_UA_Master_Plan.md                    ← Master overview document (read first)
├── ADDENDUM_User_Token_Architecture.md          ← User token decision & rules
├── HANDOFF_01_Channel_Inventory_Utility.md      ← Inventory specification
├── HANDOFF_02_Discord_Intelligence_Daemon.md    ← Phase 1: monitoring daemon spec
├── HANDOFF_03_Discord_Command_Control.md        ← Phase 2: command & control spec
├── HANDOFF_04_Discord_MCP_Tool_Setup.md         ← MCP tool setup (quick win)
├── PRD_Discord_Event_Pipeline.md                ← Phase 4: event pipeline PRD
├── PROMPT_Channel_Inventory_For_AI_Coder.md     ← Prompt for AI coder (inventory task)
└── inventory/
    ├── __init__.py
    └── inventory_tool.py                        ← Ready-to-run inventory script
```

### Step 1: Get Your Discord User Token (You Must Do This Manually)

The user token gives the system access to every server and channel your Discord account can see.

**Browser method (recommended):**
1. Open https://discord.com/app in your browser (Chrome/Edge/Firefox)
2. Press F12 to open Developer Tools
3. Click the **Network** tab
4. In the Discord app, click on any server or channel to trigger API requests
5. In the Network tab, click on any request to `discord.com/api/...`
6. In the request headers, find `Authorization:` — copy that entire value
7. That string is your user token

**Store it securely:**
- Add to Infisical as `DISCORD_USER_TOKEN`
- OR save it in a `.env` file that's in `.gitignore` (never commit tokens)

### Step 2: Run the Channel Inventory

```bash
cd /path/to/universal_agent/discord_intelligence

# Install the dependency
pip install discord.py-self --break-system-packages
# OR in a venv:
# python -m venv .venv && source .venv/bin/activate && pip install discord.py-self

# Run the inventory
DISCORD_USER_TOKEN=your_token_here python inventory/inventory_tool.py
```

This produces two files:
- `inventory/discord_channel_inventory.json` — Full data
- `inventory/discord_channel_inventory.csv` — Open in a spreadsheet

### Step 3: Annotate the CSV

Open the CSV in Google Sheets, Excel, or any spreadsheet app. For each channel row, fill in:
- **Monitor**: YES (include in daemon), NO (skip), EVENTS_ONLY
- **Tier**: A (announcements — instant alerts), B (technical — batch triage), C (community — cheap model scanning), E (events only)
- **Notes**: Any context for yourself

Save the annotated CSV back. This becomes the configuration for the Intelligence Daemon.

### Step 4: Hand Off to Your AI Coding Agent

Give your coding agent this instruction:

---

**Prompt for AI Coding Agent:**

I'm building a Discord Intelligence subsystem for my Universal Agent project.

**Repository:** https://github.com/Kjdragan/universal_agent

**New subsystem location:** `discord_intelligence/` in the repo root

**Read these documents in this order:**
1. `discord_intelligence/Discord_UA_Master_Plan.md` — Overall vision and architecture
2. `discord_intelligence/ADDENDUM_User_Token_Architecture.md` — Critical: we're using a user token approach with discord.py-self, not a standard bot token
3. `discord_intelligence/HANDOFF_02_Discord_Intelligence_Daemon.md` — The main build spec for Phase 1

**Current state:**
- The Discord bot application "UA Disc Agent" has been created in the Developer Portal
- The bot has been authorized on my own Discord server (kdragan's server)
- I have both a bot token (for future Command & Control) and a user token (for the Intelligence Daemon)
- The channel inventory has been run and I've annotated which channels to monitor
- The annotated CSV is at `discord_intelligence/inventory/discord_channel_inventory.csv`

**What to build (Phase 1):**
Build the Discord Intelligence Daemon as specified in HANDOFF_02. This is a discord.py-self based daemon that:
1. Connects via the user token and passively monitors configured channels
2. Stores all messages in a SQLite database (schema in HANDOFF_02)
3. Runs Layer 2 deterministic signal detection (announcements, releases, events)
4. Alerts Simone on important signals via the existing UA communication infrastructure
5. Includes a scheduled Layer 3 LLM triage batch process using cheap models via ZAI
6. Produces daily intelligence digests for the morning briefing pipeline
7. Runs as a systemd service on the VPS

**Critical constraints:**
- Use `discord.py-self` library, NOT standard `discord.py`
- Follow the behavioral rules in the ADDENDUM (no outbound messages via user token, gentle API usage with delays, etc.)
- Use cheap model (Claude Haiku 4.5) via ZAI for Layer 3 triage — don't burn premium model concurrency
- Store the user token in Infisical as `DISCORD_USER_TOKEN` following existing UA patterns
- This is a NEW clean subsystem — don't hook into or modify the existing CSI codebase
- The daemon should report health to the UA heartbeat service
- Owner timezone is America/Chicago (Houston, CST/CDT)

**Reference the existing UA codebase for patterns:**
- Email/AgentMail infrastructure: `docs/03_Operations/82_Email_Architecture_And_AgentMail_Source_Of_Truth_2026-03-06.md`
- Proactive pipeline: `docs/02_Subsystems/Proactive_Pipeline.md`
- Infisical secrets: `docs/03_Operations/85_Infisical_Secrets_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`
- Heartbeat service: `docs/02_Subsystems/Heartbeat_Service.md`
- CSI architecture (for pattern reference, not integration): `docs/04_CSI/CSI_Master_Architecture.md`

---

### Step 5 (Later): MCP Tool Quick Win

This can be done independently at any time. See `HANDOFF_04_Discord_MCP_Tool_Setup.md`. It's a 15-minute setup that gives your coding agents the ability to query Discord channels interactively.

### Step 6 (Later): Command & Control Server

After the daemon is stable, build out your Discord server as a command & control interface. See `HANDOFF_03_Discord_Command_Control.md`. This uses the **bot token** (UA Disc Agent), not the user token.

### Step 7 (Later): Event Pipeline

The ambitious autonomous event discovery and digestion system. See `PRD_Discord_Event_Pipeline.md`. This needs technical investigation before implementation.
