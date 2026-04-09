# Prompt: Discord Intelligence — Final Integration Tasks

## Current State (as of 2026-04-09)

**DEPLOYED AND RUNNING:**
- `ua-discord-intelligence.service` — Passive monitoring daemon (user token, discord.py-self)
- `ua-discord-cc-bot.service` — Command & Control bot (bot token, slash commands, channel feeds)
- Channel topology created in kdragan's server (Operations, Intelligence, Artifacts, System categories)
- `.mcp.json` configured with mcp-discord under uvx
- Scheduled events API hooks implemented in daemon.py
- Text-based event detection in signals.py
- Background polling loop dispatching events to #event-calendar
- CI/CD pipeline validated through staging and production

**Repository:** https://github.com/Kjdragan/universal_agent
**Subsystem:** `discord_intelligence/`

---

## Remaining Tasks (in priority order)

### Task 1: Verify User Token Is Live (BLOCKER — do first)

The status report says the user token was extracted but needs to be upserted into Infisical. Without this, the intelligence daemon can't connect and the entire monitoring pipeline is dead.

1. Upsert `DISCORD_USER_TOKEN` into Infisical across all environments (dev, staging, prod)
2. On the VPS, restart the daemon: `sudo systemctl restart ua-discord-intelligence`
3. Verify it connected successfully:
   ```bash
   sudo journalctl -u ua-discord-intelligence --since "5 minutes ago" | head -30
   ```
   You should see: "Logged in as kdragan1" and "Connected to 45 servers"
4. After 10 minutes, verify messages are flowing:
   ```bash
   sqlite3 /path/to/discord_intelligence.db "SELECT COUNT(*) FROM messages WHERE ingested_at > datetime('now', '-10 minutes');"
   ```

**If the daemon was already running with the old token from our earlier session, it may still be connected. Check before restarting.**

### Task 2: Validate End-to-End Pipeline

Before building more features, confirm the full pipeline works:

1. **Layer 1 (Ingestion):** Messages accumulating in the database from monitored channels
2. **Layer 2 (Signals):** Check `SELECT COUNT(*), signal_type FROM signals GROUP BY signal_type;`
3. **Layer 3 (Triage):** Check `SELECT COUNT(*) FROM insights;` — if zero, verify the triage scheduler is running and ZAI is accessible
4. **CC Bot feeds:** Check kdragan's Discord server:
   - `#announcements-feed` — should have signal embeds
   - `#event-calendar` — should have any detected events
   - `#research-feed` — should have Layer 3 insights (if triage has run)
   - Slash commands — try `/status` in any channel
5. **Simone alerts:** Has Simone received any AgentMail from the Discord subsystem?
6. **Briefing integration:** Does `latest_briefing.md` contain Discord intelligence?

Report what works and what doesn't. Fix any broken links before proceeding.

### Task 3: Phase 3 — MCP Tool Finalization

The `.mcp.json` is configured but needs validation.

1. **Test the MCP tool from Claude Code or the IDE:**
   - Can it list channels in kdragan's server?
   - Can it read messages from channels?
   - Can it query channels from other servers the user is in? (This is the key question — if the MCP uses the bot token, it can only see kdragan's server)

2. **If MCP can only see bot-accessible servers (likely):** Build a lightweight SQLite MCP bridge tool:
   - A simple MCP server that exposes the daemon's `discord_intelligence.db` as a queryable tool
   - Tools: `search_messages(query, server, channel, since)`, `get_signals(type, since)`, `get_insights(limit)`, `get_events(upcoming=True)`
   - This gives agents access to ALL 912 monitored channels through stored data
   - This is actually more powerful than live Discord queries because it includes historical depth
   - Put this in `discord_intelligence/mcp_bridge.py`
   - Add to `.mcp.json` alongside the existing mcp-discord entry

3. **Update HANDOFF_04 documentation** with actual test results and the bridge tool if built.

### Task 4: Event Digest Pipeline (event_digest.py)

This is the core of Phase 4's value — turning raw event data into intelligence artifacts.

**Build `discord_intelligence/event_digest.py` with these capabilities:**

1. **Post-event text digestion:**
   - Triggered after a tracked event's end time passes (or on a schedule, checking for completed events)
   - Query the messages database for all messages in the event's channel during the event's time window (start_time to end_time, plus 15 minutes buffer on each side)
   - If fewer than 10 messages found, skip (not enough content to digest)
   - Send message batch to LLM via ZAI — use **Sonnet** (not Haiku) because digest quality matters here
   - Prompt should extract:
     * Key takeaways (3-5 bullet points)
     * Notable insights with speaker attribution
     * Action items relevant to the UA project
     * New tools, libraries, or resources mentioned (with links if available)
     * Q&A summary (if the event was a Q&A/AMA format)
   - Output as structured JSON + formatted markdown

2. **Artifact generation:**
   - Save digest as markdown file: `discord_intelligence/digests/{server_name}_{event_name}_{date}.md`
   - Push summary to LLM Wiki (external knowledge vault) using existing wiki integration patterns
   - Create Task Hub items for any detected action items
   - Update the `scheduled_events` table: `digest_generated = 1`, `digest_content = <summary>`

3. **Briefing integration:**
   - Add a "Discord Events" section to the daily briefing output
   - Include: yesterday's digested events, upcoming events (next 7 days), any owner-reacted events
   - Format matches existing briefing pipeline conventions

4. **Run as a scheduled task:**
   - Can run within the cc_bot's existing task loop, or as a separate timer
   - Check every 2 hours for completed events that haven't been digested yet
   - Respect rate limits on ZAI — process one event at a time with delays between

### Task 5: GWS Calendar Sync

Wire the event pipeline to Google Calendar using the GWS CLI.

1. **Auto-create calendar entries** for discovered events above a relevance threshold:
   - Use `gws calendar +insert` with structured JSON
   - Calendar event should include: title, description (from Discord event), start/end time (converted to CST/CDT), location (server name + channel), and a link back to the Discord event
   - Mark as "tentative" so the owner can accept/decline

2. **Reaction-driven sync from cc_bot:**
   - When owner reacts with ✅ on an event in `#event-calendar`:
     * Create/update the calendar entry as "accepted"
   - When owner reacts with ❌:
     * Delete or decline the calendar entry
   - When owner reacts with 🎙️:
     * Create calendar entry + flag the event for recording in the database

3. **Verify GWS CLI availability:**
   - Confirm `gws` is installed and authenticated on the VPS
   - Test: `gws calendar +list` — does it return the owner's calendar?
   - If not installed/authenticated, document what's needed

### Task 6: Audio Recording (Stretch — Only If Proven Feasible)

This depends on the investigation in `EVENT_PIPELINE_INVESTIGATION.md`.

1. **If audio recording works:** When a 🎙️-flagged event goes ACTIVE, the daemon joins the stage/voice channel, records to `discord_intelligence/recordings/`, and after the event ends, transcribes via Whisper (existing audio-to-text skill) and feeds through the same digest pipeline.

2. **If audio recording doesn't work:** Skip entirely. The text-based pipeline is the MVP and captures most of the value. Document the limitation.

---

## Architecture Notes

- The daemon (user token) handles all passive monitoring and data collection
- The cc_bot (bot token) handles all outbound communication and user interaction
- Never send messages through the user token
- Event digests use Sonnet (mid-tier) via ZAI — quality matters for these
- Regular Layer 3 triage uses Haiku (cheap) via ZAI — speed and cost matter there
- All new scheduled tasks should integrate with the existing async task loop pattern
- Owner timezone: America/Chicago (CST/CDT)
- Owner Discord ID: 351727866549108737

## Reference Documents

- `discord_intelligence/Discord_UA_Master_Plan.md`
- `discord_intelligence/ADDENDUM_User_Token_Architecture.md`
- `discord_intelligence/PRD_Discord_Event_Pipeline.md`
- `discord_intelligence/EVENT_PIPELINE_INVESTIGATION.md`
- `docs/02_Subsystems/Discord_Intelligence_System.md`
- `docs/02_Subsystems/Proactive_Pipeline.md`
- `docs/02_Subsystems/LLM_Wiki_System.md`
