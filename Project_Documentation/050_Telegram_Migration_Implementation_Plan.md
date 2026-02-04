# implementation_plan: Telegram Migration to Clawdbot Architecture

**Goal:** Refactor the Universal Agent Telegram Bot to match the robust, modular, and scalable architecture of Clawdbot.

## 1. Directory Structure Refactor

We will move from a flat file structure to a modular package structure in `src/universal_agent/bot/`.

```
src/universal_agent/bot/
├── __init__.py
├── main.py                  # Entry point (FastAPI + Runner)
├── core/
│   ├── __init__.py
│   ├── runner.py            # Sequential update processing
│   ├── context.py           # Extended Context object
│   ├── middleware.py        # Middleware chain logic
│   └── session.py           # Session storage & resolution
├── normalization/
│   ├── __init__.py
│   ├── message.py           # Normalizes Telegram Update -> AgentMessage
│   └── formatting.py        # Response formatting
├── plugins/                 # Feature modules
│   ├── __init__.py
│   ├── onboarding.py        # /start, welcome
│   ├── commands.py          # /agent, /status
│   └── events.py            # Reactions, system events
└── config.py                # Configuration & Env Vars
```

## 2. Phased Implementation Plan

### Phase 1: Foundation & Structure

* **Create Directory Structure:** Set up the new folders.
* **Implement Core Runner:** Replace `ptb_app.run_polling` with a custom runner loop that pulls updates and feeds them into a `ProcessingQueue` (one queue per chat_id to ensure sequential processing).
* **Implement Middleware Chain:** Create a simple `Middleware` class that allows chaining `check_auth` -> `logging` -> `normalization`.

### Phase 2: Session & State Management

* **Persistent Session Store:** Implement a file-based (JSON/SQLite) session store to map `telegram_chat_id` -> `agent_session_id`.
* **Context Resolution:** Middleware that checks `session_store` and attaches the correct `agent_session_id` to the request context.
* **Topic/Thread Support:** Ensure `message_thread_id` is part of the session key (Clawdbot parity).

### Phase 3: Message Normalization & Agent Integration

* **Normalizer:** Implement `TelegramToAgentEvent` converter. It should handle Text, Images, and Documents using `mcp.types` or internal dataclasses.
* **Agent Bridge:** Refactor `AgentAdapter` to accept these normalized objects instead of raw implementations.
* **In-Process Gateway Integration:** Ensure the bridge flows strictly through `InProcessGateway` (or External if configured).

### Phase 4: Plugins & Polishing

* **Migrate Commands:** Move `/start` and `/help` to `plugins/onboarding.py`.
* **Migrate Logic:** Move `/agent` logic to `plugins/commands.py`.
* **Throttling:** Add a Throttling middleware (optional for MVP, but good for parity) to prevent API bans.
* **Verification:** Run the bot and verify it behaves identically to before but with persistent sessions and better error handling.

## 3. Tech Stack Deep Dive

* **Telegram Library:** We will keep `python-telegram-bot` (PTB) for the raw API client, but wrap it.
  * *Why?* It's mature, async, and robust. We just need to stop using its high-level `Application` callbacks in a spaghetti way and instead use them as "Update Providers".
* **Concurrency:** We will use `asyncio.Queue` for the "Runner" aspect.
  * `queues[chat_id]` stores pending updates for that chat.
  * A worker for that chat processes them sequentially.

## 4. Risks & Mitigations

* **Risk:** Regression in existing functionality (notifications, retries).
  * *Mitigation:* Keep the existing `main.py` runnable (`main_legacy.py`) until Phase 4 is verified.
* **Risk:** Complexity overload.
  * *Mitigation:* Start with flattened middleware (just a list of functions) before full plugin engine.

## 5. Definition of Done

* Directory structure matches the plan.
* Bot handles concurrent chats without race conditions (deduplicated processing).
* Session IDs are persistent across restarts.
* Code is modular (commands are in plugins).
* Documentation updated.
