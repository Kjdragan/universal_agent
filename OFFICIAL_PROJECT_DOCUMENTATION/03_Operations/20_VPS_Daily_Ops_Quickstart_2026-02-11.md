# 20. VPS Daily Ops Quickstart (2026-02-11)

## Purpose
This is the short companion to the full deployment explainers.
Use this for daily operation and fast troubleshooting of the VPS deployment.

Primary references:
1. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/18_Hostinger_VPS_Composio_Webhook_Deployment_Runbook_2026-02-11.md`
2. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/19_Universal_Agent_VPS_App_API_Telegram_Deployment_Explainer_2026-02-11.md`

---

## What Should Be Running
1. `universal-agent-gateway.service`
2. `universal-agent-api.service`
3. `universal-agent-webui.service`
4. `universal-agent-telegram.service`

Public URLs:
1. `https://api.clearspringcg.com`
2. `https://app.clearspringcg.com`

---

## Login
```bash
ssh root@187.77.16.29
cd /opt/universal_agent
```

---

## Daily Health Check (Copy/Paste)
```bash
systemctl status universal-agent-gateway --no-pager
systemctl status universal-agent-api --no-pager
systemctl status universal-agent-webui --no-pager
systemctl status universal-agent-telegram --no-pager

curl -sS https://api.clearspringcg.com/api/v1/health
curl -I https://app.clearspringcg.com
```

Expected:
1. All services show `active (running)`.
2. API health returns JSON with `"status":"healthy"`.
3. App URL returns `HTTP 200`.

---

## Security Check (Ops Auth)
```bash
curl -i https://api.clearspringcg.com/api/v1/ops/deployment/profile
```
Expected:
1. `401 Unauthorized` without token.

```bash
cd /opt/universal_agent
set -a; source .env; set +a
curl -i -H "x-ua-ops-token: $UA_OPS_TOKEN" https://api.clearspringcg.com/api/v1/ops/deployment/profile
```
Expected:
1. `200 OK`.
2. Profile shows `vps`.

---

## Restart Commands

Restart everything:
```bash
systemctl restart universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram
```

Restart only one service:
```bash
systemctl restart universal-agent-gateway
systemctl restart universal-agent-api
systemctl restart universal-agent-webui
systemctl restart universal-agent-telegram
```

---

## Logs (Live Tail)
```bash
journalctl -u universal-agent-gateway -f
journalctl -u universal-agent-api -f
journalctl -u universal-agent-webui -f
journalctl -u universal-agent-telegram -f
```

Nginx ingress check:
```bash
tail -f /var/log/nginx/access.log
```

---

## Webhook Quick Test

Manual path:
```bash
cd /opt/universal_agent
set -a; source .env; set +a

curl -i -X POST "https://api.clearspringcg.com/api/v1/hooks/youtube/manual" \
  -H "content-type: application/json" \
  -H "authorization: Bearer ${UA_HOOKS_TOKEN}" \
  -d '{"video_url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","mode":"explainer_only","allow_degraded_transcript_only":true}'
```

Expected:
1. `HTTP 202` and `{"ok": true, ...}`.

---

## Most Common Failures and Fast Fixes

### A) Web UI returns 502
Cause:
1. `universal-agent-webui` is down.

Fix:
```bash
systemctl restart universal-agent-webui
journalctl -u universal-agent-webui -n 120 --no-pager
```

### B) Composio trigger shows queued but nothing runs
Check:
1. Nginx access log for `/api/v1/hooks/composio`.
2. Gateway logs for webhook accept/dedupe.
3. Composio subscription URL matches `https://api.clearspringcg.com/api/v1/hooks/composio`.

### C) Ops endpoints suddenly open without token
Fix:
1. Verify `.env` still has `UA_OPS_TOKEN`.
2. Verify deployment profile remains `UA_DEPLOYMENT_PROFILE=vps`.
3. Restart gateway.

---

## Security Rotation Reminder
Rotate immediately if leaked:
1. `TELEGRAM_BOT_TOKEN`
2. `COMPOSIO_API_KEY`
3. `COMPOSIO_WEBHOOK_SECRET`
4. `UA_DASHBOARD_PASSWORD`
5. `UA_OPS_TOKEN`

After rotation:
```bash
systemctl restart universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram
```

---

## Service Files
1. `/etc/systemd/system/universal-agent-gateway.service`
2. `/etc/systemd/system/universal-agent-api.service`
3. `/etc/systemd/system/universal-agent-webui.service`
4. `/etc/systemd/system/universal-agent-telegram.service`

## Nginx Files
1. `/etc/nginx/sites-available/universal-agent` (api)
2. `/etc/nginx/sites-available/universal-agent-app` (app)
