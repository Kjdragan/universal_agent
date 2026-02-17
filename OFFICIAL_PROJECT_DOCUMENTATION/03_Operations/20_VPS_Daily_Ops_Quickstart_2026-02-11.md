# 20. VPS Daily Ops Quickstart (2026-02-11)

## Purpose
This is the short companion to the full deployment explainers.
Use this for daily operation and fast troubleshooting of the VPS deployment.

Primary references:
1. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/18_Hostinger_VPS_Composio_Webhook_Deployment_Runbook_2026-02-11.md`
2. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/19_Universal_Agent_VPS_App_API_Telegram_Deployment_Explainer_2026-02-11.md`
3. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/21_Web_Chat_And_Session_Security_Hardening_Explainer_2026-02-11.md`
4. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/23_Agent_Workspace_Inspector_Skill_2026-02-11.md`
5. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/26_VPS_Host_Security_Hardening_Runbook_2026-02-12.md`
6. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/29_YouTube_Hook_Mirroring_VPS_To_Local_Reverse_Tunnel_Runbook_2026-02-13.md`
7. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/30_Local_Dev_Startup_With_Youtube_Forwarding_Tunnel_2026-02-13.md`

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
ssh root@100.106.113.93
cd /opt/universal_agent
```

Tailscale note:
1. Use `root@100.106.113.93` for all operator SSH commands.
2. Treat legacy public IP examples as historical only.

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

## Workspace Inspector (Read-Only Debug)
Use this when you want the agent/runtime to inspect a session workspace safely.

Quick smoke test on VPS:
```bash
cd /opt/universal_agent
sid="$(find AGENT_RUN_WORKSPACES -mindepth 1 -maxdepth 1 -type d -name 'session_*' -printf '%f\n' | head -n 1)"
echo "Session: $sid"
PYTHONPATH=src .venv/bin/python -c "import mcp_server as s; print(s.inspect_session_workspace(session_id='$sid', include_transcript=False, tail_lines=5))"
```

Expected:
1. JSON output with `"ok": true`.
2. `files.run.log.tail` contains recent lines.
3. `trace.json` preview and recent `work_products` snapshot are present when available.

Notes:
1. `transcript.md` is included by default (`include_transcript=true`).
2. Tool is read-only and session/path scoped.

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

## YouTube Hook Mirroring Quick Check (VPS -> Local)
Use this when you expect a playlist-add event to trigger both VPS processing and local mirroring.

On VPS:
```bash
journalctl -u universal-agent-gateway --since '15 minutes ago' --no-pager | grep -E 'Hook ingress accepted|composio-youtube-trigger|Hook forward ok|Hook forward failed|Hook forward error' | tail -n 120 || true
```

If forwarding is failing because your laptop is offline, that is expected; VPS processing should still continue.

## Optional: Local Mirror for Remote Debugging

From your local dev machine, mirror VPS workspaces and durable artifact outputs into this repo so local debugging can inspect remote `run.log`, `trace.json`, and generated deliverables:

Preferred low-resource workflow (default OFF):

```bash
scripts/remote_workspace_sync_control.sh off
scripts/remote_workspace_sync_control.sh sync-now
scripts/remote_workspace_sync_control.sh on --interval 600
scripts/remote_workspace_sync_control.sh off
```

One-command manual pull (defaults):
1. Workspaces -> `AGENT_RUN_WORKSPACES`
2. Durable artifacts -> `tmp/remote_vps_artifacts`

```bash
cd /home/kjdragan/lrepos/universal_agent
scripts/pull_remote_workspaces_now.sh
```

Custom target directories:
```bash
cd /home/kjdragan/lrepos/universal_agent
UA_LOCAL_MIRROR_DIR=/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES \
UA_LOCAL_ARTIFACTS_MIRROR_DIR=/home/kjdragan/lrepos/universal_agent/tmp/remote_vps_artifacts \
scripts/pull_remote_workspaces_now.sh
```

Single-session manual pull:
```bash
cd /home/kjdragan/lrepos/universal_agent
scripts/pull_remote_workspaces_now.sh session_20260212_001337_2e657ddc
```

When local timer is enabled, use dashboard `Config` -> `Remote To Local Debug Sync` toggle to allow/deny sync cycles remotely.
Default toggle state is OFF when unset.

```bash
cd /home/kjdragan/lrepos/universal_agent
scripts/sync_remote_workspaces.sh --once \
  --host root@100.106.113.93 \
  --remote-dir /opt/universal_agent/AGENT_RUN_WORKSPACES \
  --remote-artifacts-dir /opt/universal_agent/artifacts \
  --local-dir /home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES \
  --local-artifacts-dir /home/kjdragan/lrepos/universal_agent/tmp/remote_vps_artifacts \
  --manifest-file /home/kjdragan/lrepos/universal_agent/tmp/remote_sync_state/synced_workspaces.txt
```

Automate every 30s with user systemd timer:

```bash
scripts/install_remote_workspace_sync_timer.sh \
  --host root@100.106.113.93 \
  --remote-dir /opt/universal_agent/AGENT_RUN_WORKSPACES \
  --remote-artifacts-dir /opt/universal_agent/artifacts \
  --local-dir /home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES \
  --local-artifacts-dir /home/kjdragan/lrepos/universal_agent/tmp/remote_vps_artifacts \
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
2. Gateway logs for `received` vs `skipped` vs `accepted`.
3. Composio subscription URL matches `https://api.clearspringcg.com/api/v1/hooks/composio`.

Fast check:
```bash
journalctl -u universal-agent-gateway --since '15 minutes ago' --no-pager \
  | grep -En "Hook ingress|composio-youtube-trigger|Dispatching hook action|Creating webhook session|Hook action dispatched|skipped|deduped|unauthorized"
```

Interpret:
1. `received` + `skipped`: delivery works; parser/transform mismatch.
2. `accepted` + `Creating webhook session`: ingestion and dispatch are working.
3. no `received`: delivery path/subscription/network issue.

Important Composio UI note:
1. `webhookInvocationResponse.status=QUEUED` means webhook delivery was queued/sent.
2. `sdkTriggerInvocation ... No subscribers are listening` does not prove VPS webhook failure by itself.

Known YouTube payload pitfall:
1. Playlist events may include `item.id` (playlist-item id, long string) and `item.snippet.resourceId.videoId` (real 11-char YouTube video id).
2. Use `resourceId.videoId` as canonical `video_id`.

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
5. `/etc/systemd/system/universal-agent-service-watchdog.service`
6. `/etc/systemd/system/universal-agent-service-watchdog.timer`

## Auto-Revive Watchdog (2026-02-12)
Purpose:
1. Revive always-on services if they are inactive.
2. Restart gateway/api/webui when local health endpoints fail repeatedly.

Install/update from repo:
```bash
cd /opt/universal_agent
chmod +x scripts/vps_service_watchdog.sh scripts/install_vps_service_watchdog.sh
scripts/install_vps_service_watchdog.sh
```

Verify timer and recent watchdog actions:
```bash
systemctl status universal-agent-service-watchdog.timer --no-pager
journalctl -u universal-agent-service-watchdog.service --since '10 minutes ago' --no-pager
```

Default watchdog checks:
1. `universal-agent-gateway` -> `http://127.0.0.1:8002/api/v1/health`
2. `universal-agent-api` -> `http://127.0.0.1:8001/api/health`
3. `universal-agent-webui` -> `http://127.0.0.1:3000/`
4. `universal-agent-telegram` -> systemd active-state only

Tunable env vars (optional in `/opt/universal_agent/.env`):
1. `UA_WATCHDOG_HEALTH_FAIL_THRESHOLD` (default `3`)
2. `UA_WATCHDOG_HTTP_TIMEOUT_SECONDS` (default `3`)
3. `UA_WATCHDOG_HTTP_OK_MAX_STATUS` (default `499`)
4. `UA_WATCHDOG_POST_RESTART_SETTLE_SECONDS` (default `2`)
5. `UA_WATCHDOG_STATE_DIR` (default `/var/lib/universal-agent/watchdog`)

## Nginx Files
1. `/etc/nginx/sites-available/universal-agent` (api)
2. `/etc/nginx/sites-available/universal-agent-app` (app)
