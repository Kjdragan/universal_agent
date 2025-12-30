# Telegram Bot Integration

> [!IMPORTANT]
> **Last Updated**: 2025-12-30  
> **Status**: ✅ Production (Railway)

This directory documents the Universal Agent's Telegram bot integration, which provides a mobile-friendly interface to the agent.

---

## Architecture Overview

```
┌──────────────┐     HTTPS POST     ┌─────────────────────────────┐
│   Telegram   │ ─────────────────► │  Railway Container          │
│   Cloud      │                    │  ┌───────────────────────┐  │
│              │                    │  │  FastAPI (Uvicorn)    │  │
│              │                    │  │  - /webhook endpoint  │  │
│              │                    │  │  - /health endpoint   │  │
│              │                    │  └───────────┬───────────┘  │
│              │                    │              │               │
│              │                    │  ┌───────────▼───────────┐  │
│              │   Send Message     │  │  python-telegram-bot  │  │
│              │ ◄───────────────── │  │  Command Handlers     │  │
│              │                    │  └───────────┬───────────┘  │
└──────────────┘                    │              │               │
                                    │  ┌───────────▼───────────┐  │
                                    │  │  TaskManager (Queue)  │  │
                                    │  └───────────┬───────────┘  │
                                    │              │               │
                                    │  ┌───────────▼───────────┐  │
                                    │  │  AgentAdapter         │  │
                                    │  │  (Claude SDK Bridge)  │  │
                                    │  └───────────────────────┘  │
                                    └─────────────────────────────┘
```

---

## Key Components

| File | Purpose |
|------|---------|
| [main.py](file:///home/kjdragan/lrepos/universal_agent/src/universal_agent/bot/main.py) | FastAPI app, lifespan, webhook endpoint |
| [config.py](file:///home/kjdragan/lrepos/universal_agent/src/universal_agent/bot/config.py) | Environment variables (tokens, URLs) |
| [telegram_handlers.py](file:///home/kjdragan/lrepos/universal_agent/src/universal_agent/bot/telegram_handlers.py) | Command handlers (`/start`, `/agent`, `/status`) |
| [task_manager.py](file:///home/kjdragan/lrepos/universal_agent/src/universal_agent/bot/task_manager.py) | Async task queue with status tracking |
| [agent_adapter.py](file:///home/kjdragan/lrepos/universal_agent/src/universal_agent/bot/agent_adapter.py) | Bridge to main agent session |

---

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Display welcome message and available commands |
| `/help` | Same as `/start` |
| `/agent <prompt>` | Queue a task for the agent to execute |
| `/status` | Show status of your last 5 tasks |

---

## Security Model

1. **User Whitelist**: Only users in `ALLOWED_USER_IDS` can use the bot
2. **Webhook Secret**: Telegram sends a secret token header validated by FastAPI
3. **Non-root Container**: Bot runs as `appuser`, not root

---

## More Documentation

- [01_BOT_ARCHITECTURE.md](./01_BOT_ARCHITECTURE.md) — Detailed component breakdown
- [02_RAILWAY_DEPLOYMENT.md](./02_RAILWAY_DEPLOYMENT.md) — Production deployment guide
- [03_LOCAL_DEVELOPMENT.md](./03_LOCAL_DEVELOPMENT.md) — Testing locally with ngrok
- [04_COMMANDS_AND_USAGE.md](./04_COMMANDS_AND_USAGE.md) — User guide for bot commands
