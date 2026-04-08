# Prompt: Set Up Discord Channel Inventory Utility

## Context

I'm building a Discord integration for my Universal Agent (UA) system. Production repo: https://github.com/Kjdragan/universal_agent

I've already created a Discord bot application in the Developer Portal and have:
- Bot token (stored in Infisical as `DISCORD_BOT_TOKEN`)  
- Application ID
- The bot invited to my own Discord server and any other servers I have admin access to
- All three Privileged Gateway Intents enabled (Message Content, Server Members, Presence)

## What I Need You To Do

Build and run a Discord channel inventory utility that catalogs every server and channel my bot has access to. This is a prerequisite for the larger Discord Intelligence Daemon I'm building.

### The Utility Should:

1. **Connect to Discord** using the bot token from Infisical (or environment variable `DISCORD_BOT_TOKEN` as fallback)
2. **Enumerate all servers** (guilds) the bot is in, with: name, ID, member count, description
3. **For each server, catalog all channels** grouped by category, including: channel name, ID, type (text/forum/stage/voice), topic/description, position
4. **Check for Discord Scheduled Events** in each server and list any upcoming events
5. **Output two files:**
   - `discord_channel_inventory.json` — Full structured data with blank annotation fields (`_tier`, `_monitor`, `_notes`) that I'll fill in manually
   - `discord_channel_inventory.csv` — Simplified spreadsheet view with columns: Server, Server ID, Members, Category, Channel, Channel ID, Type, Topic, Monitor (blank), Tier (blank), Notes (blank)
6. **Print a summary** showing total servers, total channels, and scheduled events found

### Technical Requirements:
- Python using `discord.py` >= 2.7.0
- Async — use `discord.Client` with appropriate intents
- The script runs once and exits (it's an inventory tool, not a daemon)
- Store the output files in the project directory
- Follow our existing Infisical secret loading pattern if practical, otherwise just use the environment variable

### Location in Repo:
Put this in `discord_intelligence/inventory/` — this will be the start of a new `discord_intelligence` subsystem directory.

### Important Notes:
- This is a read-only operation — the bot doesn't send any messages or modify anything
- Sort servers by member count (largest first) for easy scanning
- The annotation fields (prefixed with `_`) are for me to fill in after the inventory runs. They should be empty in the output.
- The CSV should be easy to open in a spreadsheet for annotation

### After This Runs:
I'll annotate the CSV with monitoring preferences (which channels to monitor, at what tier), and that annotated file becomes the configuration input for the Discord Intelligence Daemon that will be built next.

### Reference Documents:
The full technical specification for this utility is in `HANDOFF_01_Channel_Inventory_Utility.md` (attached or in the repo). The broader project plan is in `Discord_UA_Master_Plan.md`. Both should be in the repo's `discord_intelligence/` directory or provided separately.
