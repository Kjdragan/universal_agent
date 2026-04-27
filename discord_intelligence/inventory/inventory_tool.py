"""
Discord Channel Inventory Utility
==================================
Connects to Discord using the owner's user token, enumerates all servers
and channels, and produces structured catalog files for planning the
Discord Intelligence Daemon.

Usage:
    DISCORD_USER_TOKEN=your_token_here python inventory_tool.py

Output:
    discord_channel_inventory.json  — Full structured data with annotation fields
    discord_channel_inventory.csv   — Simplified spreadsheet for annotation

Part of the discord_intelligence subsystem for Universal Agent.
"""

import asyncio
import csv
from datetime import datetime
import json
import os
import sys

import discord

# discord.py-self does not require intents for user accounts
client = discord.Client()


@client.event
async def on_ready():
    print(f"Logged in as {client.user} (ID: {client.user.id})")
    print(f"Connected to {len(client.guilds)} servers\n")

    inventory = {
        "generated_at": datetime.utcnow().isoformat(),
        "user": str(client.user),
        "total_servers": len(client.guilds),
        "servers": []
    }

    for i, guild in enumerate(client.guilds):
        print(f"  [{i+1}/{len(client.guilds)}] Scanning: {guild.name} ({guild.member_count} members)")

        server_data = {
            "name": guild.name,
            "id": str(guild.id),
            "member_count": guild.member_count,
            "owner": str(guild.owner) if guild.owner else "Unknown",
            "description": guild.description or "",
            "categories": [],
            # Owner annotation fields (fill in manually after generation)
            "_tier": "",        # A, B, C, or SKIP
            "_priority": "",    # 1-5 (1=highest)
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
            if isinstance(channel, (discord.TextChannel, discord.ForumChannel,
                                     discord.StageChannel, discord.VoiceChannel)):
                channel_data = {
                    "name": channel.name,
                    "id": str(channel.id),
                    "type": str(channel.type),
                    "topic": getattr(channel, 'topic', None) or "",
                    "position": channel.position,
                    # Owner annotation fields
                    "_monitor": "",     # YES, NO, or EVENTS_ONLY
                    "_tier": "",        # A (announcements), B (technical), C (community), E (events)
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

        # Small delay between servers to be gentle on the API
        await asyncio.sleep(1)

    # Sort servers by member count (largest first)
    inventory["servers"].sort(key=lambda s: s["member_count"] or 0, reverse=True)

    # Determine output directory (same directory as script)
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Write JSON output
    json_path = os.path.join(script_dir, "discord_channel_inventory.json")
    with open(json_path, "w") as f:
        json.dump(inventory, f, indent=2)
    print(f"\nJSON inventory written to {json_path}")

    # Write simplified CSV for quick review
    csv_path = os.path.join(script_dir, "discord_channel_inventory.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
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
                        channel["type"],
                        channel["topic"][:100] if channel["topic"] else "",
                        "", "", ""  # Owner fills these in
                    ])
    print(f"CSV inventory written to {csv_path}")

    # Summary
    total_channels = sum(
        len(cat["channels"])
        for s in inventory["servers"]
        for cat in s["categories"]
    )
    total_events = sum(len(s.get("scheduled_events", [])) for s in inventory["servers"])
    print(f"\n{'='*50}")
    print(f"INVENTORY SUMMARY")
    print(f"{'='*50}")
    print(f"  Servers:          {len(inventory['servers'])}")
    print(f"  Total channels:   {total_channels}")
    print(f"  Scheduled events: {total_events}")
    print(f"{'='*50}")

    # List servers for quick reference
    print(f"\nServers (by member count):")
    for s in inventory["servers"]:
        event_count = len(s.get("scheduled_events", []))
        channel_count = sum(len(cat["channels"]) for cat in s["categories"])
        events_str = f" | {event_count} events" if event_count > 0 else ""
        print(f"  {s['member_count']:>8} members | {channel_count:>3} channels{events_str} | {s['name']}")

    await client.close()


if __name__ == "__main__":
    token = os.environ.get("DISCORD_USER_TOKEN")
    if not token:
        print("ERROR: Set DISCORD_USER_TOKEN environment variable")
        print("Usage: DISCORD_USER_TOKEN=your_token_here python inventory_tool.py")
        sys.exit(1)

    client.run(token)
