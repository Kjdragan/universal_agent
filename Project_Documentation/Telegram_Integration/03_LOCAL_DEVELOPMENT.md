# Local Development Guide

**Last Updated**: 2025-12-30

This guide explains how to run and test the Telegram bot locally.

---

## Prerequisites

1. Python 3.12+
2. `uv` package manager
3. ngrok account (free tier works)
4. Telegram bot token from @BotFather

---

## Setup

### 1. Configure Environment

Create/update `.env` in project root:

```bash
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USER_IDS=your_telegram_user_id
WEBHOOK_SECRET=any-random-string

# For local testing with ngrok:
WEBHOOK_URL=https://your-subdomain.ngrok-free.app/webhook

# Agent Config
ANTHROPIC_API_KEY=...
COMPOSIO_API_KEY=...
ZAI_API_KEY=...
```

> [!TIP]
> Get your Telegram user ID by messaging @userinfobot

### 2. Start ngrok Tunnel

```bash
ngrok http 8080
```

Copy the `https://...ngrok-free.app` URL and update `WEBHOOK_URL` in `.env`.

### 3. Run the Bot

```bash
cd /home/kjdragan/lrepos/universal_agent
uv run uvicorn universal_agent.bot.main:app --host 0.0.0.0 --port 8080 --reload
```

---

## Testing

### Verify Health Endpoint
```bash
curl http://localhost:8080/health
```

### Verify Webhook Registration
```bash
source .env
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

### Test via Telegram
1. Open your bot in Telegram
2. Send `/start`
3. Send `/agent What is the weather today?`

---

## Development Workflow

1. Make code changes
2. Uvicorn auto-reloads (if using `--reload`)
3. Test via Telegram
4. Check logs in terminal

---

## Polling Mode (Alternative)

If you don't want to use ngrok, you can use polling mode:

1. **Remove** `WEBHOOK_URL` from `.env` (or set to empty)
2. The bot will automatically use `start_polling()` instead of webhooks

> [!WARNING]
> Polling mode is slower and uses more resources. Use webhooks for production.

---

## Common Issues

### "⛔ Unauthorized access"
Your Telegram user ID is not in `ALLOWED_USER_IDS`. Add it and restart.

### "DNS FAILED" on startup
Network issue. Check your internet connection.

### ngrok tunnel expires
Free ngrok tunnels change URLs on restart. Update `WEBHOOK_URL` each time, or use a reserved domain (paid).
