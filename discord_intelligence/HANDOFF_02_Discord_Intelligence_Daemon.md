# HANDOFF 02: Discord Intelligence Daemon

**Parent Document:** `Discord_UA_Master_Plan.md`
**Priority:** Phase 1 (after Channel Inventory is complete)
**Complexity:** Medium-High — new subsystem with daemon process, database, scheduled processing, UA integration
**Prerequisites:** Channel inventory completed and annotated; Discord bot token in Infisical

---

## Purpose

Build a persistent Discord monitoring daemon that runs 24/7 on the VPS, capturing messages from configured channels, detecting deterministic signals, and feeding intelligence into the UA's proactive pipeline. This is the core "intelligent listener" that turns Discord into a research goldmine.

## Architecture Overview

```
Discord Gateway (WebSocket)
        │
        ▼
┌─────────────────────────────┐
│   Discord Intelligence      │
│   Daemon (discord.py)       │
│                             │
│  ┌─────────────────────┐    │
│  │ Layer 1: Ingestion   │    │  ← Captures ALL messages from monitored channels
│  │ (Zero LLM cost)      │    │     Stores in SQLite with full metadata
│  └──────────┬──────────┘    │
│             │               │
│  ┌──────────▼──────────┐    │
│  │ Layer 2: Deterministic│   │  ← Regex/keyword detection for announcements,
│  │ Signals (Zero LLM)   │   │     releases, events. Discord Scheduled Events API.
│  │                      │   │     IMMEDIATE alerting to Simone.
│  └──────────┬──────────┘    │
│             │               │
│  ┌──────────▼──────────┐    │
│  │ Layer 3: LLM Triage  │   │  ← Scheduled batch processing. Cheap model
│  │ (Low cost, scheduled) │   │     (Claude 4.5 Haiku via ZAI). Relevance scoring,
│  │                      │   │     summarization, insight extraction.
│  └──────────┬──────────┘    │
│             │               │
└─────────────┼───────────────┘
              │
              ▼
┌─────────────────────────────┐
│   UA Integration Points     │
│                             │
│  • Simone alerting (email/  │
│    AgentMail/Discord DM)    │
│  • Morning briefing feed    │
│  • Task Hub mission triggers│
│  • LLM Wiki knowledge base  │
│  • Proactive pipeline input │
└─────────────────────────────┘
```

## Database Schema

A new SQLite database: `discord_intelligence.db`

**This is a NEW database, separate from CSI's database.** It follows similar patterns but is purpose-built for Discord.

```sql
-- Servers being monitored
CREATE TABLE servers (
    id TEXT PRIMARY KEY,           -- Discord server/guild ID
    name TEXT NOT NULL,
    tier TEXT DEFAULT 'B',         -- A, B, C (from inventory annotation)
    priority INTEGER DEFAULT 3,    -- 1-5 (1=highest)
    member_count INTEGER,
    monitoring_enabled BOOLEAN DEFAULT 1,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP
);

-- Channels being monitored within servers
CREATE TABLE channels (
    id TEXT PRIMARY KEY,           -- Discord channel ID
    server_id TEXT NOT NULL REFERENCES servers(id),
    name TEXT NOT NULL,
    type TEXT,                     -- text, forum, stage, voice
    topic TEXT,
    tier TEXT DEFAULT 'B',         -- A (announcements), B (technical), C (community), E (events)
    monitoring_enabled BOOLEAN DEFAULT 1,
    last_message_at TIMESTAMP,
    UNIQUE(id, server_id)
);

-- Raw message store (Layer 1)
CREATE TABLE messages (
    id TEXT PRIMARY KEY,           -- Discord message ID
    channel_id TEXT NOT NULL REFERENCES channels(id),
    server_id TEXT NOT NULL REFERENCES servers(id),
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    author_is_bot BOOLEAN DEFAULT 0,
    content TEXT,                  -- Message text content
    embed_data TEXT,               -- JSON of any embeds
    attachment_urls TEXT,          -- JSON array of attachment URLs
    reference_id TEXT,             -- Reply-to message ID (if reply)
    created_at TIMESTAMP NOT NULL,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Layer 2 fields
    signal_type TEXT,              -- NULL, 'announcement', 'release', 'event', 'mention'
    signal_alerted BOOLEAN DEFAULT 0,
    -- Layer 3 fields
    triage_batch_id TEXT,          -- Which batch processed this
    relevance_score REAL,          -- 0.0 - 1.0
    summary TEXT,                  -- LLM-generated one-line summary
    topics TEXT,                   -- JSON array of detected topics
    is_actionable BOOLEAN,
    triage_processed_at TIMESTAMP
);

-- Layer 2: Detected signals
CREATE TABLE signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT REFERENCES messages(id),
    channel_id TEXT NOT NULL,
    server_id TEXT NOT NULL,
    signal_type TEXT NOT NULL,     -- 'announcement', 'release', 'event_scheduled', 
                                  -- 'event_started', 'version_mention', 'breaking_change'
    signal_data TEXT,              -- JSON with extracted details
    severity TEXT DEFAULT 'info',  -- 'info', 'important', 'urgent'
    alerted_at TIMESTAMP,
    alert_channel TEXT,            -- How it was delivered: 'email', 'discord_dm', 'task_hub'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Layer 3: Triage batches
CREATE TABLE triage_batches (
    id TEXT PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    message_count INTEGER,
    model_used TEXT,               -- e.g., 'claude-haiku-4-5-20251001'
    tokens_used INTEGER,
    insights_generated INTEGER,
    status TEXT DEFAULT 'running'  -- 'running', 'completed', 'failed'
);

-- Layer 3: Extracted insights (higher-level than individual message triage)
CREATE TABLE insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT REFERENCES triage_batches(id),
    insight_type TEXT NOT NULL,    -- 'trend', 'release_summary', 'expert_finding', 
                                  -- 'bug_report', 'workaround', 'new_tool', 'discussion_summary'
    title TEXT NOT NULL,
    content TEXT NOT NULL,         -- Detailed insight text
    source_message_ids TEXT,       -- JSON array of contributing message IDs
    source_channels TEXT,          -- JSON array of channel names
    source_servers TEXT,           -- JSON array of server names
    relevance_score REAL,
    topics TEXT,                   -- JSON array
    surfaced_in_briefing BOOLEAN DEFAULT 0,
    surfaced_at TIMESTAMP,
    pushed_to_wiki BOOLEAN DEFAULT 0,
    pushed_to_wiki_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Discord Scheduled Events tracking
CREATE TABLE scheduled_events (
    id TEXT PRIMARY KEY,           -- Discord event ID
    server_id TEXT NOT NULL REFERENCES servers(id),
    name TEXT NOT NULL,
    description TEXT,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    status TEXT,                   -- 'scheduled', 'active', 'completed', 'canceled'
    location TEXT,
    event_type TEXT,               -- 'stage', 'voice', 'external'
    creator_id TEXT,
    -- Calendar integration
    calendar_entry_created BOOLEAN DEFAULT 0,
    owner_notified BOOLEAN DEFAULT 0,
    recording_requested BOOLEAN DEFAULT 0,
    recording_path TEXT,
    -- Post-event processing
    digest_generated BOOLEAN DEFAULT 0,
    digest_content TEXT,
    digest_pushed_to_wiki BOOLEAN DEFAULT 0,
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Processing watermarks (track what's been processed)
CREATE TABLE watermarks (
    channel_id TEXT PRIMARY KEY,
    last_processed_message_id TEXT,
    last_processed_at TIMESTAMP,
    last_triage_batch_id TEXT
);

-- Indexes for performance
CREATE INDEX idx_messages_channel ON messages(channel_id, created_at);
CREATE INDEX idx_messages_server ON messages(server_id, created_at);
CREATE INDEX idx_messages_triage ON messages(triage_batch_id);
CREATE INDEX idx_messages_unprocessed ON messages(triage_processed_at) WHERE triage_processed_at IS NULL;
CREATE INDEX idx_signals_type ON signals(signal_type, created_at);
CREATE INDEX idx_signals_unalerted ON signals(alerted_at) WHERE alerted_at IS NULL;
CREATE INDEX idx_insights_type ON insights(insight_type, created_at);
CREATE INDEX idx_insights_unsurfaced ON insights(surfaced_in_briefing) WHERE surfaced_in_briefing = 0;
CREATE INDEX idx_events_status ON scheduled_events(status, start_time);
```

## Layer 1: Message Ingestion

### Core Event Handler

```python
@client.event
async def on_message(message):
    # Skip bot messages (optional — some bot messages are valuable, like release bots)
    # if message.author.bot:
    #     return
    
    # Check if this channel is monitored
    channel_config = get_channel_config(message.channel.id)
    if not channel_config or not channel_config['monitoring_enabled']:
        return
    
    # Store the message (Layer 1)
    store_message(message, channel_config)
    
    # Run Layer 2 deterministic checks
    signals = detect_signals(message, channel_config)
    if signals:
        for signal in signals:
            store_signal(signal)
            if signal['severity'] in ('important', 'urgent'):
                await alert_simone(signal)  # Immediate notification
```

### What Gets Stored

Every message from a monitored channel is stored with:
- Full text content
- Embed data (many announcements use rich embeds)
- Attachment URLs
- Author info (name, ID, bot status)
- Reply/thread context
- Precise timestamp

**Storage cost estimate**: A typical Discord message record is ~500 bytes. 10,000 messages/day = ~5MB/day = ~1.8GB/year. Trivial for a VPS with standard disk space.

## Layer 2: Deterministic Signal Detection

### Signal Detection Rules

These are Python functions — no LLM calls. They run on every incoming message in real-time.

```python
def detect_signals(message, channel_config):
    signals = []
    content_lower = message.content.lower() if message.content else ""
    
    # Rule 1: Announcement channel messages are always signals
    if channel_config['tier'] == 'A':
        signals.append({
            'type': 'announcement',
            'severity': 'important',
            'data': {
                'server': message.guild.name,
                'channel': message.channel.name,
                'author': str(message.author),
                'preview': message.content[:500] if message.content else "[embed]"
            }
        })
    
    # Rule 2: Version/release patterns
    version_patterns = [
        r'v?\d+\.\d+\.\d+',           # v1.2.3 or 1.2.3
        r'version\s+\d+',              # version 2
        r'release\s+\d+',              # release 3
    ]
    release_keywords = ['released', 'launching', 'now available', 'just shipped',
                       'announcing', 'introducing', 'breaking change', 'deprecat']
    
    has_version = any(re.search(p, content_lower) for p in version_patterns)
    has_release_keyword = any(kw in content_lower for kw in release_keywords)
    
    if has_version and has_release_keyword:
        signals.append({
            'type': 'release',
            'severity': 'important',
            'data': {'preview': message.content[:500]}
        })
    
    # Rule 3: Event mentions
    event_keywords = ['event', 'webinar', 'workshop', 'ama', 'office hours',
                     'livestream', 'live stream', 'demo day', 'launch event']
    time_keywords = ['today', 'tomorrow', 'this week', 'join us', 'register',
                    'rsvp', 'sign up', 'at \d+\s*(am|pm|pst|est|utc)']
    
    has_event = any(kw in content_lower for kw in event_keywords)
    has_time = any(re.search(p, content_lower) for p in time_keywords) if isinstance(time_keywords[-1], str) else any(kw in content_lower for kw in time_keywords[:-1])
    
    if has_event and has_time:
        signals.append({
            'type': 'event_mention',
            'severity': 'info',
            'data': {'preview': message.content[:500]}
        })
    
    # Rule 4: Direct mentions of technologies/topics the owner cares about
    # This list should be configurable and grow over time
    interest_keywords = [
        'claude code', 'agent sdk', 'mcp', 'model context protocol',
        'openclaw', 'anthropic api', 'claude 4', 'claude opus',
        'letta', 'memgpt', 'hermes', 'agentic',
        # Add more based on owner's interests
    ]
    
    matched_interests = [kw for kw in interest_keywords if kw in content_lower]
    if matched_interests and channel_config['tier'] != 'A':  # Don't double-signal announcements
        signals.append({
            'type': 'interest_match',
            'severity': 'info',
            'data': {
                'matched_keywords': matched_interests,
                'preview': message.content[:500]
            }
        })
    
    return signals
```

### Discord Scheduled Events Monitoring

```python
@client.event
async def on_scheduled_event_create(event):
    """Fires when any monitored server creates a new scheduled event."""
    store_scheduled_event(event)
    await alert_simone({
        'type': 'event_scheduled',
        'severity': 'important',
        'data': {
            'server': event.guild.name,
            'event_name': event.name,
            'description': event.description,
            'start_time': event.start_time.isoformat(),
            'end_time': event.end_time.isoformat() if event.end_time else None,
            'location': str(event.location) if event.location else None
        }
    })
    # Trigger calendar integration (Phase 4 / Event Pipeline)

@client.event
async def on_scheduled_event_update(before, after):
    """Fires when an event is updated (including when it starts)."""
    update_scheduled_event(after)
    if before.status != after.status:
        if str(after.status) == 'active':
            await alert_simone({
                'type': 'event_started',
                'severity': 'urgent',
                'data': {
                    'server': after.guild.name,
                    'event_name': after.name
                }
            })
```

## Layer 3: LLM Triage (Scheduled Batch Processing)

### Design Principles

- Runs on a **configurable schedule** (default: every 4 hours during owner's active hours, once overnight)
- Uses **cheap model** (Claude 4.5 Haiku or equivalent) via ZAI proxy
- Processes messages that haven't been triaged yet (using watermarks)
- Groups messages by channel for context
- Produces both per-message scores AND cross-channel insights

### Batch Processing Flow

```python
async def run_triage_batch():
    batch_id = generate_batch_id()
    
    # 1. Gather unprocessed messages, grouped by channel
    channel_batches = get_unprocessed_messages_by_channel()
    
    for channel_id, messages in channel_batches.items():
        channel_config = get_channel_config(channel_id)
        
        # 2. Build prompt based on channel tier
        if channel_config['tier'] == 'C':
            # Community chat: aggressive filtering, look for expert signals
            prompt = build_community_triage_prompt(messages, channel_config)
        elif channel_config['tier'] == 'B':
            # Technical: moderate filtering, look for actionable info
            prompt = build_technical_triage_prompt(messages, channel_config)
        else:
            # Tier A messages already handled by Layer 2, but still summarize
            prompt = build_announcement_summary_prompt(messages, channel_config)
        
        # 3. Call cheap model via ZAI
        result = await call_zai(
            model="claude-haiku-4-5-20251001",  # Cheap, fast, high concurrency
            prompt=prompt,
            max_tokens=2000
        )
        
        # 4. Parse results, update messages with scores/summaries
        parsed = parse_triage_result(result)
        update_message_triage(messages, parsed, batch_id)
        
        # 5. Generate cross-channel insights if significant findings
        if parsed.get('insights'):
            store_insights(parsed['insights'], batch_id)
    
    # 6. Generate daily digest if this is the morning batch
    if is_morning_batch():
        digest = await generate_daily_digest(batch_id)
        await deliver_to_briefing_pipeline(digest)
```

### Triage Prompt Strategy (Community Chat — Tier C)

This is the most important prompt because it handles the highest volume, lowest signal content. The key insight from the owner: **community chat contains expert signals that are extremely valuable but buried in noise.**

```python
def build_community_triage_prompt(messages, channel_config):
    return f"""You are a Discord intelligence analyst for an AI developer. 
Scan these messages from #{channel_config['name']} in {channel_config['server_name']}.

Your job: identify the ~5% of messages that contain GENUINE SIGNAL. 

Signal types to look for:
- EXPERT_FINDING: An experienced user sharing a non-obvious insight, workaround, or best practice
- BUG_REPORT: Someone reporting a real bug or regression with specifics
- SOLUTION: A working solution to a technical problem
- NEW_TOOL: Mention of a tool, library, or resource the owner might not know about
- BREAKING_CHANGE: Information about breaking changes, deprecations, migration requirements
- PERFORMANCE_TIP: Concrete performance optimization or cost-saving technique
- ARCHITECTURAL_INSIGHT: Design pattern discussion, architectural decision rationale

For each signal found, return:
- message_id
- signal_type
- relevance_score (0.0-1.0)
- one_line_summary
- why_valuable (one sentence explaining why this matters)

Messages to scan:
{format_messages_for_prompt(messages)}

Respond ONLY with JSON. If no signals found, return {{"signals": []}}."""
```

### Cost Estimate

- **Per batch**: ~500 messages across 20 channels = ~10 API calls to Haiku = ~$0.01-0.05
- **Per day** (6 batches): ~$0.06-0.30
- **Per month**: ~$2-10
- With ZAI generous limits, this is effectively negligible

## Integration with UA Systems

### Simone Alerting

Layer 2 signals and high-relevance Layer 3 insights get routed to Simone. The delivery mechanism should use the **existing UA communication pathways**:

```python
async def alert_simone(signal):
    """Route a signal to Simone through the UA's existing alert infrastructure."""
    # Option 1: Email/AgentMail (existing, reliable)
    # Option 2: Task Hub item creation
    # Option 3: Discord DM (if command & control server is set up)
    
    # Format the alert
    alert_body = format_signal_for_simone(signal)
    
    # Determine delivery based on severity
    if signal['severity'] == 'urgent':
        # Multiple channels for urgent signals
        await send_agentmail(alert_body)
        await create_task_hub_item(alert_body, priority='high')
    elif signal['severity'] == 'important':
        await send_agentmail(alert_body)
    else:
        # Info-level: batch into daily digest
        queue_for_digest(signal)
```

### Morning Briefing Integration

The daemon produces a daily intelligence digest that feeds into the existing morning briefing pipeline:

```python
async def generate_daily_digest(batch_id):
    """Generate the Discord intelligence section for the morning briefing."""
    
    # Gather today's signals and insights
    signals = get_signals_since_last_briefing()
    insights = get_insights_since_last_briefing()
    upcoming_events = get_upcoming_events(days=7)
    
    digest = {
        "section_title": "Discord Intelligence",
        "generated_at": datetime.utcnow().isoformat(),
        "summary_stats": {
            "messages_ingested": count_messages_since_last_briefing(),
            "signals_detected": len(signals),
            "insights_generated": len(insights),
            "upcoming_events": len(upcoming_events)
        },
        "urgent_signals": [s for s in signals if s['severity'] == 'urgent'],
        "important_signals": [s for s in signals if s['severity'] == 'important'],
        "top_insights": sorted(insights, key=lambda i: i['relevance_score'], reverse=True)[:10],
        "upcoming_events": upcoming_events,
        "notable_discussions": get_high_engagement_threads()
    }
    
    return digest
```

### Mission Trigger Integration

When Layer 2 detects a significant signal (e.g., new SDK release), it can automatically create a mission in the Task Hub:

```python
async def trigger_research_mission(signal):
    """Create an autonomous research mission based on a Discord signal."""
    
    if signal['type'] == 'release':
        mission = {
            "title": f"Research: {signal['data'].get('preview', '')[:100]}",
            "description": f"Auto-triggered by Discord signal from {signal['data'].get('server', 'unknown')}. "
                          f"Investigate this release, assess relevance to UA project, "
                          f"and produce a briefing artifact.",
            "source": "discord_intelligence",
            "source_signal_id": signal['id'],
            "priority": "medium",
            "delegatable_to": "ATLAS",  # Research VP
            "auto_approved": False  # Requires owner review before execution
        }
        await create_task_hub_mission(mission)
```

### LLM Wiki Integration

High-value insights can be automatically pushed to the knowledge base:

```python
async def push_insight_to_wiki(insight):
    """Store a valuable insight in the LLM Wiki knowledge base."""
    
    wiki_entry = {
        "title": insight['title'],
        "content": insight['content'],
        "source": "discord_intelligence",
        "source_details": {
            "channels": insight['source_channels'],
            "servers": insight['source_servers'],
            "message_ids": insight['source_message_ids'],
            "discovered_at": insight['created_at']
        },
        "topics": insight['topics'],
        "vault": "external"  # Goes in external knowledge vault
    }
    
    await upsert_wiki_entry(wiki_entry)
    
    # Mark as pushed
    mark_insight_pushed_to_wiki(insight['id'])
```

## Deployment on VPS

### Process Management

The daemon should run as a **systemd service** alongside the existing UA services:

```ini
# /etc/systemd/system/ua-discord-intelligence.service
[Unit]
Description=UA Discord Intelligence Daemon
After=network.target

[Service]
Type=simple
User=ua
WorkingDirectory=/path/to/universal_agent
ExecStart=/path/to/venv/bin/python -m discord_intelligence.daemon
Restart=always
RestartSec=10
Environment=DISCORD_BOT_TOKEN_FROM_INFISICAL=true

[Install]
WantedBy=multi-user.target
```

### Layer 3 Timer

```ini
# /etc/systemd/system/ua-discord-triage.timer
[Unit]
Description=UA Discord Intelligence Triage (every 4 hours)

[Timer]
OnCalendar=*-*-* 06,10,14,18,22:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

### Directory Structure (within UA repo)

```
discord_intelligence/
├── __init__.py
├── daemon.py              # Main bot process (Layer 1 + Layer 2)
├── config.py              # Channel configuration loader
├── database.py            # SQLite schema and operations
├── signals.py             # Layer 2 signal detection rules
├── triage.py              # Layer 3 batch processing
├── integration/
│   ├── __init__.py
│   ├── simone_alerts.py   # Route signals to Simone
│   ├── briefing.py        # Morning briefing digest generation
│   ├── task_hub.py        # Mission trigger integration
│   └── wiki.py            # LLM Wiki push integration
├── models/
│   ├── __init__.py
│   ├── message.py
│   ├── signal.py
│   └── insight.py
├── inventory/
│   ├── __init__.py
│   └── inventory_tool.py  # The channel inventory utility from HANDOFF_01
└── tests/
    ├── test_signals.py
    ├── test_triage.py
    └── test_integration.py
```

## Configuration

The daemon reads its configuration from the annotated channel inventory plus a YAML config file:

```yaml
# discord_intelligence/config.yaml
daemon:
  log_level: INFO
  heartbeat_interval: 300  # seconds, report health to UA heartbeat service

ingestion:
  store_bot_messages: true  # Some bots post valuable release info
  max_message_age_days: 90  # Prune messages older than this
  
signals:
  interest_keywords:
    - claude code
    - agent sdk
    - mcp
    - model context protocol
    - openclaw
    - anthropic api
    - letta
    - hermes
    - agentic
    # Extend as interests evolve
  
  alert_channels:
    urgent: [email, discord_dm, task_hub]
    important: [email]
    info: [digest_only]

triage:
  model: "claude-haiku-4-5-20251001"
  schedule: "0 6,10,14,18,22 * * *"  # Cron format
  max_messages_per_batch: 500
  min_relevance_threshold: 0.3  # Below this, message is classified as noise
  
briefing:
  morning_briefing_time: "07:00"
  timezone: "America/Chicago"  # Owner is in Houston (CST/CDT)
  max_insights_per_briefing: 15
  include_upcoming_events: true
  event_lookahead_days: 7

wiki:
  auto_push_threshold: 0.8  # Insights above this score auto-push to wiki
  require_review_below: 0.8  # Below this, queue for owner review

channel_inventory_path: "discord_channel_inventory.json"
```

---

## Implementation Notes for Agent (Claude Code / Other)

1. **Start with the daemon skeleton**: Get the bot connecting, receiving messages, and storing them in SQLite. This is the MVP — everything else builds on reliable ingestion.

2. **Layer 2 can be iterative**: Start with a few signal detection rules and add more over time. The keyword lists should be easily configurable.

3. **Layer 3 integration with ZAI**: The implementing agent needs to understand how the UA currently calls LLM APIs via the ZAI proxy. Check `src/` for existing patterns.

4. **Simone alerting integration**: The most critical integration point. Check the existing email/AgentMail infrastructure in the UA to understand how to route alerts. Check `docs/03_Operations/82_Email_Architecture_And_AgentMail_Source_Of_Truth_2026-03-06.md`.

5. **Morning briefing integration**: Check `docs/02_Subsystems/Proactive_Pipeline.md` for how the existing briefing pipeline works and where Discord intelligence should plug in.

6. **The owner is in Houston, Texas (CST/CDT)**: All time-based scheduling should account for this timezone.

7. **Infisical for secrets**: The Discord bot token should be stored in Infisical following the existing UA pattern. Check `docs/03_Operations/85_Infisical_Secrets_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`.

8. **No residential proxy needed**: Discord API uses standard WebSocket connections. The rotating residential proxy is not required for this integration.

9. **Cheap model for Tier C processing is a KEY DECISION**: The owner specifically called out using Claude 4.5 Haiku (or equivalent cheap model) for community chat scanning to preserve high-concurrency premium model slots for coding work. This is not a suggestion — it's a requirement.

10. **The daemon should report its health to the UA heartbeat service** so system-wide monitoring catches Discord connection issues.
