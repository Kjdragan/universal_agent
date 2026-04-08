# HANDOFF 01: Discord Channel Inventory Utility

**Parent Document:** `Discord_UA_Master_Plan.md`
**Priority:** Do First (Pre-Phase)
**Complexity:** Low — standalone utility, ~100-150 lines of Python
**Prerequisites:** Discord bot token created in Developer Portal

---

## Purpose

Before building the full monitoring daemon, we need a structured inventory of every Discord server and channel the owner belongs to. This utility bot authenticates, enumerates everything, and produces a catalog file that the owner can annotate with tier classifications and priority levels. The annotated catalog then becomes the configuration input for the monitoring daemon (Phase 1).

## Why This Is First

- The channel inventory determines the scope of the monitoring daemon
- It reveals which servers have announcement channels, event features, developer channels, etc.
- It helps the owner prioritize — they have "a lot of Discord channels" and many they don't actively follow
- The inventory output becomes a configuration artifact used by all subsequent phases
- It's small, self-contained, and immediately useful

## Technical Approach

### Bot Setup (One-Time, Manual)

The owner needs to:
1. Go to https://discord.com/developers/applications
2. Click "New Application" → name it (e.g., "UA Discord Agent")
3. Go to the "Bot" tab → click "Reset Token" → copy and save the token
4. **Enable Privileged Intents**: On the Bot tab, enable:
   - `MESSAGE_CONTENT` intent (needed for Phase 1 monitoring)
   - `SERVER MEMBERS` intent (useful for context)
   - `PRESENCE` intent (optional, can skip)
5. Go to OAuth2 → URL Generator:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Read Messages/View Channels`, `Send Messages`, `Read Message History`, `Use Slash Commands`
   - Copy the generated URL
6. Open the URL in a browser to invite the bot to their **own Discord server**

**IMPORTANT**: The bot can only see servers it has been explicitly added to. For the inventory to cover all the owner's servers, the bot needs to be added to each one. However, for *reading* from public AI community servers, the owner's user account is already a member — the bot just needs the `MESSAGE_CONTENT` intent and to be invited to those servers too.

**Alternative approach for inventory only**: Use the Discord user token (not bot token) approach via a self-bot — BUT this violates Discord TOS. The recommended path is to invite the bot to each server the owner wants to monitor. For large AI community servers with public invite links, this is straightforward.

**Practical compromise for inventory**: The owner can manually list their servers (name + invite link or server ID), and the bot can be added to the priority ones. The inventory utility can then catalog the channels within those servers. Not every server needs to be inventoried immediately.

### Inventory Script

```python
# discord_inventory.py
# Run once to produce a structured catalog of all servers and channels
# the bot has access to.

import discord
import json
import csv
from datetime import datetime
import asyncio
import os

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"Logged in as {client.user} (ID: {client.user.id})")
    print(f"Connected to {len(client.guilds)} servers\n")
    
    inventory = {
        "generated_at": datetime.utcnow().isoformat(),
        "bot_user": str(client.user),
        "total_servers": len(client.guilds),
        "servers": []
    }
    
    for guild in client.guilds:
        server_data = {
            "name": guild.name,
            "id": str(guild.id),
            "member_count": guild.member_count,
            "owner": str(guild.owner) if guild.owner else "Unknown",
            "description": guild.description or "",
            "categories": [],
            # Owner annotation fields (fill in manually after generation)
            "_tier": "",  # A, B, C, or SKIP
            "_priority": "",  # 1-5 (1=highest)
            "_notes": ""
        }
        
        # Group channels by category
        categories = {}
        uncategorized = []
        
        for channel in guild.channels:
            if isinstance(channel, discord.CategoryChannel):
                categories[channel.id] = {
                    "name": channel.name,
                    "id": str(channel.id),
                    "position": channel.position,
                    "channels": []
                }
        
        for channel in guild.channels:
            if isinstance(channel, (discord.TextChannel, discord.ForumChannel, discord.StageChannel, discord.VoiceChannel)):
                channel_data = {
                    "name": channel.name,
                    "id": str(channel.id),
                    "type": str(channel.type),
                    "topic": getattr(channel, 'topic', None) or "",
                    "position": channel.position,
                    # Owner annotation fields
                    "_monitor": "",  # YES, NO, or EVENTS_ONLY
                    "_tier": "",  # A (announcements), B (technical), C (community), E (events)
                    "_notes": ""
                }
                
                if channel.category_id and channel.category_id in categories:
                    categories[channel.category_id]["channels"].append(channel_data)
                else:
                    uncategorized.append(channel_data)
        
        # Sort channels within categories by position
        for cat_id in categories:
            categories[cat_id]["channels"].sort(key=lambda c: c["position"])
        
        server_data["categories"] = sorted(categories.values(), key=lambda c: c["position"])
        if uncategorized:
            server_data["categories"].append({
                "name": "(Uncategorized)",
                "id": "none",
                "position": 999,
                "channels": sorted(uncategorized, key=lambda c: c["position"])
            })
        
        # Check for scheduled events
        try:
            events = await guild.fetch_scheduled_events()
            server_data["scheduled_events"] = [
                {
                    "name": event.name,
                    "description": event.description or "",
                    "start_time": event.start_time.isoformat() if event.start_time else "",
                    "end_time": event.end_time.isoformat() if event.end_time else "",
                    "status": str(event.status),
                    "location": str(event.location) if event.location else ""
                }
                for event in events
            ]
        except Exception as e:
            server_data["scheduled_events"] = []
            server_data["_event_error"] = str(e)
        
        inventory["servers"].append(server_data)
    
    # Sort servers by member count (largest first)
    inventory["servers"].sort(key=lambda s: s["member_count"] or 0, reverse=True)
    
    # Write JSON output
    output_path = "discord_channel_inventory.json"
    with open(output_path, "w") as f:
        json.dump(inventory, f, indent=2)
    print(f"Inventory written to {output_path}")
    
    # Also write a simplified CSV for quick review
    csv_path = "discord_channel_inventory.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Server", "Server ID", "Members", "Category", 
            "Channel", "Channel ID", "Type", "Topic",
            "Monitor (YES/NO/EVENTS_ONLY)", "Tier (A/B/C/E)", "Notes"
        ])
        for server in inventory["servers"]:
            for category in server["categories"]:
                for channel in category["channels"]:
                    writer.writerow([
                        server["name"], server["id"], server["member_count"],
                        category["name"], channel["name"], channel["id"],
                        channel["type"], channel["topic"][:100] if channel["topic"] else "",
                        "", "", ""  # Owner fills these in
                    ])
    print(f"CSV written to {csv_path}")
    
    # Summary
    total_channels = sum(
        len(ch) 
        for s in inventory["servers"] 
        for cat in s["categories"] 
        for ch in [cat["channels"]]
    )
    total_events = sum(len(s.get("scheduled_events", [])) for s in inventory["servers"])
    print(f"\nSummary:")
    print(f"  Servers: {len(inventory['servers'])}")
    print(f"  Total channels: {total_channels}")
    print(f"  Scheduled events found: {total_events}")
    
    await client.close()

# Run
token = os.environ.get("DISCORD_BOT_TOKEN")
if not token:
    print("ERROR: Set DISCORD_BOT_TOKEN environment variable")
    print("Usage: DISCORD_BOT_TOKEN=your_token_here python discord_inventory.py")
else:
    client.run(token)
```

### Output Format

The utility produces two files:

**1. `discord_channel_inventory.json`** — Full structured data with annotation fields prefixed with `_` that the owner fills in:
- `_tier` on servers: Overall server priority
- `_monitor` on channels: YES / NO / EVENTS_ONLY  
- `_tier` on channels: A (announcements) / B (technical) / C (community) / E (events)
- `_notes` on anything: Free-form notes

**2. `discord_channel_inventory.csv`** — Simplified spreadsheet view for quick scanning and annotation. The last three columns are blank for the owner to fill in.

### Owner Workflow

1. Run the inventory script on the VPS (or locally)
2. Open the CSV in a spreadsheet
3. For each channel, mark:
   - **Monitor**: YES (include in daemon), NO (skip), EVENTS_ONLY (only watch for scheduled events)
   - **Tier**: A (announcements — Layer 2 immediate alerting), B (technical — Layer 3 batch triage), C (community — Layer 3 cheap model scanning), E (events — Scheduled Events API)
   - **Notes**: Any context (e.g., "this is where they announce new model releases")
4. Save the annotated CSV
5. The annotated CSV becomes the configuration input for the monitoring daemon

### Estimated Effort

- **Bot creation in Developer Portal**: 10 minutes (one-time)
- **Adding bot to servers**: 2-3 minutes per server (open invite link, authorize)
- **Running inventory script**: ~30 seconds
- **Annotating the CSV**: 20-60 minutes depending on how many servers/channels

### Dependencies

```
pip install discord.py>=2.7.0
```

No other dependencies. No LLM calls. No database. Pure enumeration utility.

---

## What Happens After This

The annotated inventory feeds directly into:
- **HANDOFF_02** (Monitoring Daemon): The daemon reads the annotated inventory to know which channels to monitor and at what tier
- **HANDOFF_03** (Command & Control): Understanding the channel landscape helps design the owner's server structure
- **PRD_Discord_Event_Pipeline**: Identifies which servers have active event programs worth monitoring

---

## Notes for Implementing Agent (Claude Code / Other)

- This is a standalone script, not part of the UA codebase (yet). Run it independently.
- The bot token should be stored in Infisical alongside other UA secrets, but for initial inventory run, an environment variable is fine.
- If the owner can't add the bot to certain servers (e.g., some servers restrict bot additions), those servers can be listed manually with their server IDs. The full monitoring bot may be able to use a user account token approach later (with TOS awareness).
- The script should be idempotent — safe to run multiple times as the owner adds the bot to more servers.
