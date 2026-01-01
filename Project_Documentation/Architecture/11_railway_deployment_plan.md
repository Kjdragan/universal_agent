# Railway Deployment Architecture

**Document Version**: 2.0  
**Last Updated**: 2025-12-30  
**Status**: ✅ PRODUCTION (Deployed Dec 30, 2025)

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Environment Configuration](#environment-configuration)
4. [Persistence (Volumes)](#persistence-volumes)
5. [Telegram Webhook](#telegram-webhook)
6. [Troubleshooting](#troubleshooting)

---

## Overview

The Universal Agent runs on **Railway.app** as an always-on cloud service with the following characteristics:

| Aspect | Value |
|--------|-------|
| **Platform** | Railway (Pro plan, Static IP) |
| **Region** | US West |
| **Container** | Python 3.12 + Debian Bookworm |
| **Architecture** | Monolithic (Bot + Agent College) |
| **Deployment** | Auto-deploy on `git push main` |

### Production Endpoints

| Endpoint | URL |
|----------|-----|
| Webhook | `https://web-production-3473.up.railway.app/webhook` |
| Health | `https://web-production-3473.up.railway.app/health` |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Railway Container                        │
│                                                             │
│  ┌─────────────┐         ┌─────────────────────────────┐   │
│  │ start.sh    │────────►│ Bot (FastAPI + PTB)         │   │
│  │ (entrypoint)│         │ - Uvicorn on $PORT          │   │
│  └──────┬──────┘         │ - Webhook handler           │   │
│         │                │ - TaskManager worker        │   │
│         │                │ - AgentAdapter              │   │
│         │                └─────────────────────────────┘   │
│         │                                                   │
│         │                ┌─────────────────────────────┐   │
│         └───────────────►│ Agent College (Internal)    │   │
│                          │ - Uvicorn on port 8000      │   │
│                          │ - Logfire integration       │   │
│                          └─────────────────────────────┘   │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                 /app/data (Volume)                    │  │
│  │  - Memory_System_Data/agent_core.db                   │  │
│  │  - workspaces/session_*/                              │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## Environment Configuration

Configure these in Railway Dashboard → Variables:

### Required Secrets

| Variable | Purpose |
|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Bot authentication |
| `WEBHOOK_SECRET` | Webhook verification |
| `ANTHROPIC_API_KEY` | Claude API |
| `ZAI_API_KEY` | Z.AI proxy |
| `COMPOSIO_API_KEY` | Tool router |
| `GEMINI_API_KEY` | Image generation |
| `LOGFIRE_TOKEN` | Observability |

### Required Variables

| Variable | Value |
|----------|-------|
| `WEBHOOK_URL` | `https://web-production-3473.up.railway.app/webhook` |
| `ALLOWED_USER_IDS` | Comma-separated Telegram user IDs |
| `MODEL_NAME` | `claude-sonnet-4-20250514` |
| `DEFAULT_USER_ID` | Composio user ID |

### Persistence Variables

| Variable | Value |
|----------|-------|
| `PERSIST_DIRECTORY` | `/app/data/memory` |
| `AGENT_WORKSPACE_ROOT` | `/app/data/workspaces` |

---

## Persistence (Volumes)

Railway Volume mounted at `/app/data`:

| Path | Contents |
|------|----------|
| `/app/data/memory/` | SQLite database (agent_core.db) |
| `/app/data/workspaces/` | Session artifacts |

> [!IMPORTANT]
> The `start.sh` script runs `chown -R appuser:appuser /app/data` on startup to fix permissions.

---

## Telegram Webhook

### How It Works

1. Telegram sends HTTPS POST to `/webhook`
2. FastAPI validates `X-Telegram-Bot-Api-Secret-Token` header
3. PTB processes the update
4. Command handlers dispatch to TaskManager

### Registration

The bot auto-registers the webhook on startup. If it fails (network timeout), it logs instructions for manual registration:

```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=<WEBHOOK_URL>&secret_token=<SECRET>"
```

### Verification

```bash
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

Expected response:
```json
{
  "ok": true,
  "result": {
    "url": "https://web-production-3473.up.railway.app/webhook",
    "pending_update_count": 0
  }
}
```

---

## Troubleshooting

### Container Crashes on Startup

**Symptom**: `telegram.error.TimedOut` then exit

**Cause**: Network timeout reaching Telegram API

**Fix** (as of Dec 30, 2025): Bot now runs in "degraded mode" - it stays up even if webhook registration fails. Check logs for manual registration instructions.

### 502 Bad Gateway

**Symptom**: Telegram webhook returns 502

**Cause**: Container not running or crashed

**Debug**:
1. Check Railway logs
2. Verify health endpoint: `curl .../health`
3. Redeploy if needed

### Webhook Not Receiving Updates

**Debug checklist**:
1. `getWebhookInfo` shows correct URL?
2. `pending_update_count` not growing?
3. Container logs show incoming requests?

---

## Cost Model

Railway charges for:
- **RAM Reservation** (24/7): ~$5-10/month
- **CPU Usage** (bursts): Variable

This is a "bot server" model, not serverless. The container runs continuously.

---

## Related Documentation

- [Telegram_Integration/](../Telegram_Integration/) — Bot internals
- [start.sh](file:///home/kjdragan/lrepos/universal_agent/start.sh) — Container entrypoint
- [Dockerfile](file:///home/kjdragan/lrepos/universal_agent/Dockerfile) — Build configuration
