# Railway Deployment Guide

**Last Updated**: 2025-12-30  
**Status**: ✅ Production (Deployed Dec 30, 2025)

---

## Quick Reference

| Item | Value |
|------|-------|
| **Production URL** | `https://web-production-3473.up.railway.app` |
| **Webhook Endpoint** | `/webhook` |
| **Health Check** | `/health` |
| **Region** | US West |
| **Plan** | Pro (Static IP) |

---

## Environment Variables (Railway Dashboard)

### Required Secrets
| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `WEBHOOK_SECRET` | Random string for webhook verification |
| `ANTHROPIC_API_KEY` | Claude API key |
| `ZAI_API_KEY` | Z.AI proxy key |
| `COMPOSIO_API_KEY` | Tool router key |

### Required Variables
| Variable | Description | Example |
|----------|-------------|---------|
| `WEBHOOK_URL` | Public webhook URL | `https://web-production-3473.up.railway.app/webhook` |
| `ALLOWED_USER_IDS` | Comma-separated Telegram user IDs | `123456789,987654321` |
| `PORT` | Leave blank (Railway sets automatically) | — |

### Persistence Paths
| Variable | Value |
|----------|-------|
| `PERSIST_DIRECTORY` | `/app/data/memory` |
| `AGENT_WORKSPACE_ROOT` | `/app/data/workspaces` |

---

## Deployment Flow

```
GitHub (main branch)
        │
        │ git push
        ▼
Railway (Auto-Deploy)
        │
        │ Detects Dockerfile
        ▼
Docker Build
        │
        │ Multi-stage build
        ▼
Container Start
        │
        │ start.sh
        ▼
Bot Running (24/7)
```

---

## Container Startup (`start.sh`)

1. Fix permissions on `/app/data` volume
2. Run network diagnostics (ping Google, test Telegram API)
3. Start Agent College service (port 8000 internal)
4. Start Telegram bot (port from $PORT env)

---

## Monitoring

### Health Check
```bash
curl https://web-production-3473.up.railway.app/health
# {"status": "healthy", "tasks_active": 0}
```

### Webhook Status
```bash
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

### Logs
Railway Dashboard → Deployments → Logs

---

## Troubleshooting

### Container Can't Reach Telegram API
**Symptoms**: `telegram.error.TimedOut` on startup

**Fixes** (in order):
1. Switch Railway region
2. Enable Static IP (Pro plan)
3. Bot now runs in "degraded mode" and stays up

### Webhook Returns 502
**Cause**: Container crashed or not started

**Fix**: Check logs, redeploy, or verify `WEBHOOK_URL` matches actual Railway domain

### Manual Webhook Registration
If auto-registration fails, register manually:
```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=<WEBHOOK_URL>&secret_token=<SECRET>"
```
