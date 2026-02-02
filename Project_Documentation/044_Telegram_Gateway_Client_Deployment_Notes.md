# 044 — Telegram Gateway Client Deployment Notes (2026‑02‑02)

## Purpose
Operational notes for running the Telegram bot as a **true gateway client** (Option C).

## Required Environment Variables
- `UA_GATEWAY_URL` — External gateway base URL (required for production)
- `TELEGRAM_BOT_TOKEN` — Bot token
- `WEBHOOK_URL` — Public URL for Telegram webhooks (e.g., `https://<bot-host>/webhook`)
- `WEBHOOK_SECRET` — Secret token for webhook validation
- `ALLOWED_USER_IDS` — Comma‑separated Telegram user IDs

## Local Dev Override
- `UA_TELEGRAM_ALLOW_INPROCESS=1` allows the bot to run an in‑process gateway.
- `start_telegram_bot.sh` sets this automatically for local development.

## Notes
- If `UA_GATEWAY_URL` is not set and the override is not enabled, the bot will fail fast with a clear error.
- Ensure the gateway is reachable from the bot host for all requests.
