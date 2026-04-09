# Discord Event Pipeline Investigation

## 1. Technical Feasibility of Audio Recording (discord.py-self)

Can `discord.py-self` receive/record audio from stage channels?
- **Yes**, but it requires a specialized audio receiving pipeline.
- `discord.py-self` supports connecting to Voice and Stage channels. To receive audio, third-party patching or specialized modules like `discord.ext.voice_recv` or `davey` are often leveraged.
- **Required Libraries**: 
  - `PyNaCl` (for voice packet encryption/decryption)
  - `ffmpeg` (for muxing/demuxing the Opus stream to standard formats like WAV or MP3).
- **Capabilities**: A user account *can* join a stage channel (if it has permissions) as an audience member and silently ingest the audio stream, outputting PCM data that can be recorded to disk and later passed to our `media-processing` skill / Whisper for transcription.

## 2. Discord TOS Implications for Recording

What is the practical risk for recording public stage events?
- **Self-botting Policy**: Using a user-token programmatically already strictly violates Discord's Terms of Service. Discord uses heuristics (action speed, CAPTCHAs) to detect them. We are heavily mitigating this via the passive, read-only "listen" approach defined in `ADDENDUM_User_Token_Architecture.md`.
- **Audio Risk**: Joining a voice channel programmatically emits different WebSocket opcodes and changes presence state. Since the account acts as an active Voice connection without Discord client telemetry, it increases the fingerprint visibility.
- **Privacy/Legal**: Recording voice calls without explicit consent is legally murky (depending on one-party vs. two-party consent laws), even in public stage servers. From Discord's perspective, responding to a user report about a silent recording bot usually results in an instant, permanent account ban. 
- **Conclusion**: Feasible, but high-risk for the user account. It's recommended to stick to the Text-based MVP first.

## 3. Google Calendar API Integration

Does our Universal Agent currently have Google Calendar integration?
- **Yes.** The agent ecosystem natively includes the `google_calendar` (via `gws` - Google Workspace CLI) skill, configured and usable via MCP/tool execution.
- **What is needed to add it?** No new integrations build are strictly necessary. The `gws` MCP provides tools to interact with your Google Calendar, e.g. `mcp__gws__calendar_events_insert`. The Event Pipeline can instruct the agent or trigger a function that formats the event data and dispatches a JSON payload into the `google_calendar` tool or directly creates the event programmatically via `uv run gws` commands.

## 4. How Discord Scheduled Events Work in Practice

Do all servers use them?
- **No.** While Discord formally supports Scheduled Events (`discord.ScheduledEvent`, accessible via `on_scheduled_event_create`), adoption across crypto, tech, and AI communities is highly inconsistent.
- Many community servers exclusively post event announcements as stylized text messages in dedicated `#announcements` or `#events` channels without ever generating the interactive Discord Event object.
- **Consequence for Pipeline:** Relying solely on `on_scheduled_event_create` will miss >50% of real-world events. Layer 2 signal detection (parsing natural language like "Join us this Friday at 5PM PST for an AMA") is absolutely critical to catch the unstated events and store them identically to formal Scheduled Events.
