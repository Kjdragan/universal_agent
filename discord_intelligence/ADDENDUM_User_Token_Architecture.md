# ADDENDUM: User Token Architecture Decision

**Applies to:** `Discord_UA_Master_Plan.md`, `HANDOFF_02_Discord_Intelligence_Daemon.md`
**Date:** April 8, 2026
**Decision:** Use owner's Discord user token (Path C) for the Intelligence Daemon

---

## Decision

The Intelligence Daemon will authenticate using the owner's Discord **user account token** rather than the bot token. This gives the daemon access to every server and channel the owner is a member of, eliminating the need to invite a bot to each server (which is impossible for large public servers where the owner lacks Manage Server permission).

The **bot token** remains useful for the Command & Control server (HANDOFF_03), where we control the server and want proper bot identity with slash commands. The two tokens serve different purposes:

| Token Type | Used For | Scope |
|-----------|---------|-------|
| **User token** | Intelligence Daemon (passive monitoring of all subscribed servers) | Every server/channel the owner can see |
| **Bot token** | Command & Control server (slash commands, embeds, operational interface) | Owner's kdragan server only |

## TOS Awareness & Risk Mitigation

Using a user token for automation technically violates Discord's Terms of Service. The owner accepts this risk for personal automation. The following rules **MUST** be followed to minimize detection risk:

### Behavioral Rules for the Daemon

**DO:**
- Maintain a single persistent WebSocket gateway connection (mimics desktop client)
- Receive messages passively as they're pushed via the gateway (zero API cost for incoming messages)
- Process and store messages locally — all intelligence work happens in our database and LLM pipeline, never touching Discord's API
- Use natural delays (3-10 seconds of random jitter) between any REST API calls
- Rate-limit REST API calls to no more than 1-2 per minute for any non-gateway operations
- Keep the connection alive with normal heartbeat intervals (Discord sends these; just respond normally)
- If fetching channel history (backfill), do it for one channel at a time with 5-10 second delays between pages, and limit to recent messages only

**DO NOT:**
- Mass-scrape channel history (no fetching thousands of old messages in bulk)
- Send any messages from the user account automatically (all outbound communication goes through the bot token on the CC server)
- Make rapid-fire REST API calls (no tight loops, no parallel requests)
- Join or leave servers programmatically
- Modify the user's presence, status, or profile
- React to messages, add emoji, or interact with messages in any way
- Use multiple simultaneous gateway connections (Discord allows one per user)
- Fetch member lists or do bulk data operations

**SPECIAL CARE:**
- If the daemon needs to restart, add a random delay (30-120 seconds) before reconnecting
- Don't run the daemon during Discord maintenance windows
- Monitor for 4xx/429 rate limit responses and back off aggressively (exponential backoff with jitter)
- Log all REST API calls for audit — we should be able to verify our footprint is minimal

### What This Means Practically

The daemon's network footprint is nearly identical to having the Discord desktop app open and idle. The gateway connection receives messages that would be pushed to the client anyway. The only "extra" activity compared to a normal client is that we *store* what we receive and *don't* display a UI. No REST API calls are needed for passive message ingestion — the gateway pushes everything.

The only REST API calls we might make:
1. **Occasional channel history fetch** — When the daemon starts up, it may want to catch up on messages sent while it was offline. Fetch the last 50-100 messages per monitored channel, with delays between channels. Do this sparingly (once per daemon restart, not continuously).
2. **Scheduled events fetch** — Check for new scheduled events across servers. Do this once every few hours, not continuously.
3. **Channel/server info** — Refresh channel lists if needed. Once per day is sufficient.

## Library Choice

**Use `discord.py-self`** (also called `selfcord` or `discord.py` self-bot fork) instead of standard `discord.py`. The standard library actively blocks user tokens and raises an error. The self-bot fork removes this restriction.

```bash
pip install discord.py-self
```

Repository: https://github.com/dolfies/discord.py-self

This is a maintained fork that tracks the upstream discord.py API but allows user token authentication. The API is nearly identical to standard discord.py — same event handlers, same models, same async patterns. The main differences:
- `bot=False` parameter when creating the client
- No slash command registration (that's bot-only; our CC server bot handles that)
- Some additional events available (typing indicators, read state, etc.)

## Obtaining the User Token

The owner needs to extract their Discord user token from the browser or desktop app:

### Browser Method:
1. Open Discord in a web browser (discord.com/app)
2. Open Developer Tools (F12)
3. Go to the Network tab
4. Filter by "api" or look for any XHR request to discord.com/api
5. In the request headers, find `Authorization:` — that value is the user token
6. Copy and store it securely in Infisical as `DISCORD_USER_TOKEN`

### Alternative (Desktop App):
1. Open Discord desktop app
2. Press Ctrl+Shift+I to open Developer Tools
3. Go to Console tab
4. Paste: `(webpackChunkdiscord_app.push([[''],{},e=>{m=[];for(let c in e.c)m.push(e.c[c])}]),m).find(m=>m?.exports?.default?.getToken!==void 0).exports.default.getToken()`
5. Copy the output (your token)
6. Store securely in Infisical as `DISCORD_USER_TOKEN`

**IMPORTANT:** Treat the user token with the same security as a password. Anyone with this token has full access to the Discord account. Store it only in Infisical, never in code or config files.

## Changes to HANDOFF_02

The Intelligence Daemon architecture from HANDOFF_02 remains the same, with these modifications:

1. **Authentication**: Use `DISCORD_USER_TOKEN` from Infisical instead of `DISCORD_BOT_TOKEN`
2. **Library**: Use `discord.py-self` instead of `discord.py`
3. **Client initialization**: 
   ```python
   import discord
   from discord.ext import commands
   
   client = discord.Client()  # No intents parameter needed for self-bots
   # User tokens automatically receive all events
   ```
4. **No privileged intents needed**: User accounts automatically have access to all events including message content. The privileged intent system only applies to bot accounts.
5. **Channel inventory is now automatic**: The daemon can enumerate all servers and channels on startup without needing the bot to be invited anywhere. The HANDOFF_01 inventory utility should also be updated to use the user token.
6. **REST API calls**: Add jitter and rate limiting to any REST API calls per the behavioral rules above.
7. **No outbound messages through user token**: All messages the system sends (alerts, briefings, embeds) go through the **bot token** on the CC server, never through the user account.

## Changes to HANDOFF_01

The Channel Inventory Utility can now use the user token instead of the bot token. This means:
- It will see ALL servers the owner is a member of, not just ones the bot was invited to
- The inventory will be complete on the first run
- No need to invite the bot to external servers

Update the inventory script to:
```python
# Use discord.py-self
import discord

client = discord.Client()  # Works with user token

# ... rest of the inventory logic remains the same ...

token = os.environ.get("DISCORD_USER_TOKEN")
client.run(token)
```

## Summary

| Component | Token | Library | Purpose |
|-----------|-------|---------|---------|
| Intelligence Daemon | User token | discord.py-self | Passive monitoring of all servers |
| Channel Inventory | User token | discord.py-self | Enumerate all servers/channels |
| Command & Control | Bot token | discord.py (standard) | Slash commands, embeds on owner's server |
| Discord MCP Tool | Bot token | (varies by MCP server) | On-demand agent queries |

The bot token and bot application (UA Disc Agent) remain valuable for the CC server — that's where slash commands, rich embeds, and operational messaging happen. The user token is purely for silent, passive intelligence gathering.
