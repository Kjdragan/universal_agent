# Discord Operations and Usage Guide

**Canonical source of truth** for operational usage, slash commands, and proactive task generation workflows via the Discord CC Bot.

**Last Updated:** 2026-04-09

## 1. Overview 

The Universal Agent Discord CC Bot (`ua-discord-cc-bot`) serves as your native command-and-control surface directly within Discord. Rather than SSHing into the VPS or navigating to the dashboard, you can trigger complex workflows, query intelligence databases, and assign new tasks to the Universal Agent swarm seamlessly from your phone or desktop.

This guide outlines the core commands, how they integrate into the broader Task Hub SQLite databases, and how to effectively string them together to maintain a proactive posture.

## 2. Slash Command Reference

### Task & Mission Management
The CC Bot reaches directly into the `task_hub_items` SQLite tables to map your requests.
* **`/task_list`**: View a truncated feed of your immediate standard tasks globally across the Task Hub architecture. *Good for a quick glimpse of what the agent is chewing on.*
* **`/mission_list`**: Filters directly to your high-priority, ongoing agent missions.
* **`/mission_status`**: Passing a specific `Task ID` pulls exact metadata parameters (due times, granular progress state). Use this to peek into exact parameters on a task executed out-of-band by a VP.
* **`/task_add`**: Injects an immediate command into the Task Hub. If `priority` is set >= 3, it automatically spins up a clean Discord tracking thread in `#mission-status` so the VPs have a sandbox to report back into without spamming `stdout`.
* **`/research <topic>`**: Commission an ATLAS VP execution dynamically. It injects a new mission tagged for ATLAS ingestion, creates the Discord thread tracking surface, and sets the state to `open` so it gets snapped up in the next heartbeat.

### Intelligence & Briefings
Leverages the background capabilities of the `ua-discord-intelligence` daemon's local SQLite hub and the NotebookLM integration workflows.
* **`/briefing [now|morning|weekly]`**: Pulls the raw content from the newest file dropped within `kb/briefings/` and renders it cleanly into Discord embeds. Perfect for catching up with the morning digest without opening Obsidian or checking email notifications.
* **`/discord_insights`**: Spits out the highest-scored insights (derived dynamically by the parsing agents over real-time Discord traffic) along with their originating channels and metadata links. Use this to skip scrolling and see only things evaluated as relevant to your parameters.

### Knowledge Vault Interaction
Bypass manually querying tables.
* **`/wiki_query <question>`**: Plunges into the `knowledge_updates` SQLite table and returns any corresponding records the agent has mapped regarding your question.
* **`/wiki_add <title> <content>`**: Manually stash ad-hoc notes into the ZAI records if you stumble onto something important and want to seed the active context window.

### Operational Setup
* **`/setup_webhooks`**: Run this once inside corresponding channels (like `#reports`). This utility command auto-constructs webhook URLs, persisting them onto the system. It prints the URL so you can inject it securely via `Infisical` into external configurations, granting VP agents direct delivery channels directly back into Discord.
* **`/config_triage_frequency`**: Throttle how often you want the Layer 3 summarization agents pinging the database to build reports. Useful to slow down or speed up depending on traffic variations.

## 3. The `#simone-chat` Native Pipeline

Normally, routing a chat message to Simone would require API connections spanning across frontends. The CC Bot now manages this out-of-the-box natively using Discord's raw event hooks.

**How it works:**
Whenever you type a message in the `#simone-chat` channel (and it is verified against your explicit `OWNER_ID`), the bot immediately packages the payload and dispatches it straight into the primary `Task Hub` SQLite Queue flagged with `["simone-chat", "direct-prompt"]`.

**Why it matters:**
This bypasses standard web/email interfaces entirely, acting as a low-latency "hotline". Simone's task-monitoring processes treat this with the same priority as an overarching prompt, meaning you get access to Simone's full cognitive pipeline directly from chat.

## 4. Proactive Workflows: "A Day in the Life"

The real power of these disconnected capabilities emerges when they are chained together. Here is an example of proactively orchestrating the Swarm from Discord:

### Scenario: The Morning Catch-up & Commissioning
1. **The Briefing:** You wake up and run `/briefing morning` from your phone. You see the autonomous agent has pulled down a summary of ZAI alerts and Slack notifications that occurred overnight.
2. **Review Insights:** You run `/discord_insights 3`. One of the insights mentions a major open-source repository release discussing a new agent framework logic model.
3. **Vault Verify:** To ensure the system hasn't already dealt with this framework, you run `/wiki_query "new agent logic model"`. The database returns nothing.
4. **Trigger ATLAS Exploration:** Rather than read up on it yourself, you proactively run `/research "New Agent Logic Model GitHub Repo"`.
5. **Thread Spawns:** A dedicated `#mission-status` Discord thread is autonomously generated. You can close your laptop knowing ATLAS will grab the `Task Hub` job, spin up a workspace, scrape the repository via tool-use, and (assuming `/setup_webhooks` was run previously) dump a complete markdown analysis of the repo architecture directly into your `#reports` channel later in the afternoon.

By providing native UI surfaces over backend SQLite task queues, Discord transitions from a passive alert repository into the primary tactical dashboard of operations.
