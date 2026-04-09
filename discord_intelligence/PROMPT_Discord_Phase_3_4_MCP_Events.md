# Prompt: Discord Phases 3 & 4 — MCP Tool + Event Pipeline

## Current State

Phases 1 and 2 are COMPLETE and DEPLOYED on the production VPS:
- `ua-discord-intelligence.service` — Daemon monitoring 912 channels via user token
- `ua-discord-cc-bot.service` — CC bot with slash commands and channel feeds via bot token
- Channel topology created in kdragan's server (Operations, Intelligence, Artifacts, System)
- All tokens in Infisical, CI/CD validated, docs updated

Repository: https://github.com/Kjdragan/universal_agent
Subsystem location: `discord_intelligence/`

---

## Task 1: Phase 3 — MCP Tool Setup

**Goal:** Give our VP agents (ATLAS, CODIE) and local development tools the ability to query Discord channels interactively during mission execution.

**Reference:** `discord_intelligence/HANDOFF_04_Discord_MCP_Tool_Setup.md`

### Steps:

1. **Pick the MCP server.** Evaluate:
   - `netixc/mcp-discord` (Python, lightweight, matches our stack)
   - `IQAIcom/mcp-discord` (Node.js, more feature-rich — channel management, forums, webhooks, reactions)
   - Choose whichever provides the best read capability (reading messages, searching, listing channels/servers). We primarily need READ operations for agent research. Write operations are secondary.

2. **Install and configure.** Add to `.mcp.json` in the repo root. The MCP server should use `DISCORD_BOT_TOKEN` for our own server operations. For reading messages from servers the user is in (but the bot isn't), investigate whether the MCP server supports user tokens — if so, configure with `DISCORD_USER_TOKEN` for read operations.

3. **Test these specific queries:**
   - "List all channels in kdragan's server"
   - "Read the last 10 messages from #announcements in the Claude Discord"
   - "Search for messages mentioning 'agent SDK' in the Anthropic Discord in the last 7 days"
   - "Get server info for the Model Context Protocol Discord"
   
   If the MCP server only works with the bot token and can only see kdragan's server, document this limitation. The daemon's SQLite database becomes the workaround — agents can query the database directly for messages from all servers.

4. **Create a fallback tool if needed.** If the MCP server can't read from servers the bot isn't in, build a simple MCP-compatible tool that queries our `discord_intelligence.db` SQLite database instead. This gives agents access to all 912 monitored channels through the stored messages. This might actually be MORE useful than live Discord queries since it includes historical data.

5. **Update documentation.** Record what was installed, how it's configured, what works, and any limitations in `HANDOFF_04_Discord_MCP_Tool_Setup.md`.

---

## Task 2: Phase 4 — Event Intelligence Pipeline

### Step A: Technical Investigation (do BEFORE any code)

Write findings to `discord_intelligence/EVENT_PIPELINE_INVESTIGATION.md`

**Investigate these specific questions:**

1. **Audio recording capability:**
   - Can `discord.py-self` join voice/stage channels and receive audio?
   - What dependencies are needed? (ffmpeg, opus, PyNaCl?)
   - Write a small test script that attempts to join a voice channel in kdragan's server and record 10 seconds of silence. This validates the technical capability without risking anything on external servers.
   - Document: works / doesn't work / partially works with limitations

2. **Discord TOS practical risk for recording:**
   - Research: has Discord ever banned accounts for recording public stage events?
   - What do other recording bots (Craig, Otter.ai Discord integration) do?
   - Our risk tolerance: we'd only record public stage events in large community servers (not private conversations). Is this meaningfully different from screen-recording a stream?

3. **Google Calendar integration:**
   - Check if our UA system already has Google Calendar access (check existing MCP servers, check Infisical for Google credentials)
   - The owner has Google Calendar MCP available in Claude — can we leverage the same auth?
   - Alternative: can cc_bot.py create calendar events via Google Calendar API directly?
   - If no existing integration: document what's needed (Google Cloud project, OAuth consent, Calendar API enable, credentials)

4. **Scheduled Events API coverage:**
   - How many of our 28 monitored servers actually use Discord's native Scheduled Events feature?
   - Query the daemon's database: `SELECT server_id, COUNT(*) FROM scheduled_events GROUP BY server_id`
   - For servers that DON'T use scheduled events, text-based detection (already built in Layer 2 signals.py) is the only option. How well is it working? Check: `SELECT * FROM signals WHERE signal_type LIKE '%event%' LIMIT 20`

### Step B: Build the MVP (Text-Based Events)

After investigation is complete, build:

1. **Event discovery consolidation:**
   - The daemon already captures `on_scheduled_event_create/update` events and text-based event detection exists in signals.py
   - Ensure ALL discovered events (both API-based and text-detected) end up in the `scheduled_events` table with proper metadata
   - Add a `discovery_method` column if not present: 'api' or 'text_detection'

2. **Enhanced event cards in #event-calendar:**
   - Rich embeds showing: server name, event title, description, time in CST/CDT, event type, discovery method
   - Add reaction buttons: ✅ (want to attend), 🎙️ (record this), 📋 (summarize after), ❌ (not interested)
   - The cc_bot should listen for these reactions and update the `scheduled_events` table with the owner's preference

3. **Post-event text digestion pipeline:**
   - When a tracked event ends (or its time window passes):
     a. Query the daemon's message database for all messages in the relevant channel during the event's time window
     b. Send the message batch to LLM (use a mid-tier model for this — Sonnet, not Haiku — because digest quality matters)
     c. Extract: key takeaways (3-5 points), notable insights with attribution, action items, tools/resources mentioned, Q&A summary
     d. Generate a briefing artifact as markdown
     e. Push to LLM Wiki (external knowledge vault)
     f. Create a Task Hub item if action items were detected
     g. Include in the next morning briefing

4. **Upcoming events in daily briefing:**
   - Add a section to the briefing pipeline showing events in the next 7 days
   - Sort by relevance (Tier 1 server events first, then Tier 2, then Tier 3)
   - Include any events the owner marked with reactions

### Step C: Audio Recording (Only if Investigation Shows Feasibility)

If Step A confirms audio recording works:

1. When an event the owner marked with 🎙️ goes ACTIVE:
   - The daemon joins the stage/voice channel
   - Records audio to a file on the VPS (use ffmpeg + opus)
   - Saves to a dedicated directory: `discord_intelligence/recordings/`

2. After the event ends:
   - Transcribe using Whisper (the UA has an `audio-to-text` skill — check `/mnt/skills/user/audio-to-text/SKILL.md` for the existing capability)
   - Run the same LLM digest pipeline as text events, but with the transcript
   - Store both the audio file and transcript
   - Link both in the briefing artifact

3. If recording is NOT feasible: skip this entirely. The text-based pipeline is the MVP and delivers most of the value.

---

## Constraints

- **User token rules:** All passive listening through user token. No outbound messages through user token. See `ADDENDUM_User_Token_Architecture.md`
- **Cheap model for Layer 3 triage.** Mid-tier model (Sonnet) acceptable for event digests since quality matters there.
- **Owner timezone:** America/Chicago (CST/CDT)
- **Owner Discord user ID:** 351727866549108737
- **All secrets via Infisical.** No .env files with tokens.
- **Heartbeat reporting** for any new services.

## Reference Documents in Repo

- `discord_intelligence/Discord_UA_Master_Plan.md`
- `discord_intelligence/ADDENDUM_User_Token_Architecture.md`
- `discord_intelligence/HANDOFF_04_Discord_MCP_Tool_Setup.md`
- `discord_intelligence/PRD_Discord_Event_Pipeline.md`
- `docs/02_Subsystems/Discord_Intelligence_System.md` (new canonical doc)
- `docs/02_Subsystems/Proactive_Pipeline.md`
- `docs/02_Subsystems/LLM_Wiki_System.md`
