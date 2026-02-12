# 20. VPS Daily Ops Quickstart (2026-02-11)

## Purpose
This is the short companion to the full deployment explainers.
Use this for daily operation and fast troubleshooting of the VPS deployment.

Primary references:
1. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/18_Hostinger_VPS_Composio_Webhook_Deployment_Runbook_2026-02-11.md`
2. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/19_Universal_Agent_VPS_App_API_Telegram_Deployment_Explainer_2026-02-11.md`
3. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/21_Web_Chat_And_Session_Security_Hardening_Explainer_2026-02-11.md`

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

## Post-Fix Verification (Web Query Stability)
```bash
cd /opt/universal_agent
latest_log="$(ls -1t AGENT_RUN_WORKSPACES/cron_*/run.log | head -n 1)"
echo "Using log: $latest_log"

tail -n 400 "$latest_log" | grep -Eni "reconnect|re-?connect|retry(ing)?|connection (lost|reset|refused|failed|timeout)|timed out|websocket.*(close|error)|ECONNRESET|ECONNREFUSED|EHOSTUNREACH|ENETUNREACH" || true
```

Expected:
1. No matches in the last 400 log lines during normal operation.
2. UI should process once and settle online, without repeated connecting/updating loops.
3. Heartbeat runs should complete with `HEARTBEAT_OK` when no active monitor is triggered.

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

## Optional: Local Mirror for Remote Debugging

From your local dev machine, mirror VPS workspaces into this repo so local debugging can inspect remote `run.log`, `trace.json`, and artifacts:

Preferred low-resource workflow (default OFF):

```bash
scripts/remote_workspace_sync_control.sh off
scripts/remote_workspace_sync_control.sh sync-now
scripts/remote_workspace_sync_control.sh on --interval 600
scripts/remote_workspace_sync_control.sh off
```

When local timer is enabled, use dashboard `Config` -> `Remote To Local Debug Sync` toggle to allow/deny sync cycles remotely.
Default toggle state is OFF when unset.

```bash
cd /home/kjdragan/lrepos/universal_agent
scripts/sync_remote_workspaces.sh --once \
  --host root@187.77.16.29 \
  --remote-dir /opt/universal_agent/AGENT_RUN_WORKSPACES \
  --local-dir /home/kjdragan/lrepos/universal_agent/tmp/remote_app_workspaces \
  --manifest-file /home/kjdragan/lrepos/universal_agent/tmp/remote_sync_state/synced_workspaces.txt
```

Automate every 30s with user systemd timer:

```bash
scripts/install_remote_workspace_sync_timer.sh \
  --host root@187.77.16.29 \
  --remote-dir /opt/universal_agent/AGENT_RUN_WORKSPACES \
  --local-dir /home/kjdragan/lrepos/universal_agent/tmp/remote_app_workspaces \
  --manifest-file /home/kjdragan/lrepos/universal_agent/tmp/remote_sync_state/synced_workspaces.txt \
  --interval 30
```

Notes:
1. Default behavior skips previously synced workspace IDs, even if local mirror folders were later deleted.
2. Optional remote cleanup is available with `--prune-remote-when-local-missing --allow-remote-delete` (destructive; use intentionally).
3. Prune mode only deletes remote directories older than 300s by default (`--prune-min-age-seconds`).

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
