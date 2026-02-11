# Hostinger VPS + Composio Webhook Deployment Runbook (Explainer)

**Date:** 2026-02-11  
**Status:** Live and validated  
**Audience:** First-time operator (no prior VPS webhook deployment experience assumed)

## Why This Document Exists

This is the source-of-truth explanation of exactly what was done to deploy Universal Agent webhook ingress on a Hostinger VPS, why each step mattered, what was achieved, what risks were introduced, and how to repeat the same setup safely.

## What You Have Now

1. A public HTTPS API endpoint: `https://api.clearspringcg.com`
2. A running gateway service managed by systemd: `universal-agent-gateway.service`
3. Nginx reverse proxy forwarding external traffic to the local app on `127.0.0.1:8002`
4. A valid TLS certificate from Let’s Encrypt (auto-renew configured)
5. Composio webhook subscription registered to:
   `https://api.clearspringcg.com/api/v1/hooks/composio`
6. Manual webhook endpoint enabled:
   `https://api.clearspringcg.com/api/v1/hooks/youtube/manual`
7. Both test paths returning `HTTP 202 Accepted`

## Architecture (What Talks To What)

1. Composio emits an event.
2. Composio sends signed HTTP POST to:
   `POST /api/v1/hooks/composio`
3. Nginx receives on port 443 and proxies to local gateway process.
4. Gateway verifies signature, applies webhook mapping/transform, and dispatches agent action.

Manual path:

1. You (or a script) send a bearer-token POST to:
   `POST /api/v1/hooks/youtube/manual`
2. Same gateway/mapping pipeline handles it.

## Full Step-by-Step (What We Did, Why, and Expected Result)

### 1) Point DNS to the VPS

What:

1. Created/verified GoDaddy DNS `A` record:
   `api.clearspringcg.com -> 187.77.16.29`

Why:

1. Composio requires a publicly reachable webhook URL.

Verify from local machine:

```bash
nslookup api.clearspringcg.com
```

Expected: IP resolves to `187.77.16.29`.

### 2) Access the VPS

What:

1. SSH as root:

```bash
ssh root@187.77.16.29
```

Why:

1. Deployment and service setup must run on the server.

Notes:

1. First login asks host-key confirmation.
2. Wrong password attempts are normal the first time; successful login confirms access.

### 3) Install base server components

What:

```bash
apt update
apt install -y nginx certbot python3-certbot-nginx curl git rsync
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
uv --version
```

Why:

1. `nginx` serves as reverse proxy.
2. `certbot` provisions TLS certificates.
3. `uv` manages Python environment and execution.

### 4) Sync project dependencies

What:

```bash
cd /opt/universal_agent
uv sync
```

Why:

1. Installs all Python dependencies needed by gateway/scripts.

Issue encountered:

1. `pycairo` build failed because compiler/dev libraries were missing.

Fix:

```bash
apt install -y build-essential pkg-config python3-dev libcairo2-dev libpango1.0-dev libgirepository1.0-dev libffi-dev libjpeg-dev zlib1g-dev ffmpeg
cd /opt/universal_agent
uv sync
```

### 5) Bootstrap hook mappings in UA config

What:

```bash
cd /opt/universal_agent
PYTHONPATH=src uv run python scripts/bootstrap_composio_youtube_hooks.py --write --enable-hooks --set-token-from-env
```

Why:

1. Ensures `hooks.enabled=true`.
2. Ensures required mapping IDs exist:
   `composio-youtube-trigger` and `youtube-manual-url`.
3. Keeps token management in env rather than plaintext config.

### 6) Resolve Python import path for scripts

Issue encountered:

1. Running scripts without `PYTHONPATH=src` produced:
   `ModuleNotFoundError: No module named 'universal_agent'`.

Fix options:

1. Prefix one-off commands:
   `PYTHONPATH=src uv run python ...`
2. Export once in shell:
   `export PYTHONPATH=/opt/universal_agent/src`

### 7) Run gateway as a persistent systemd service

What:

1. Created `/etc/systemd/system/universal-agent-gateway.service` with:
   `WorkingDirectory=/opt/universal_agent`
   `Environment=PYTHONPATH=/opt/universal_agent/src`
   `EnvironmentFile=/opt/universal_agent/.env`
   `ExecStart=/root/.local/bin/uv run python -m universal_agent.gateway_server`

2. Enabled and started:

```bash
systemctl daemon-reload
systemctl enable --now universal-agent-gateway
systemctl status universal-agent-gateway --no-pager
```

Why:

1. Keeps gateway alive after SSH disconnects/reboots.
2. Gives standard ops controls (`start/stop/restart/status/logs`).

### 8) Put Nginx in front of the gateway

What:

1. Created Nginx site for `api.clearspringcg.com` proxying to `127.0.0.1:8002`.
2. Enabled site and reloaded:

```bash
ln -sf /etc/nginx/sites-available/universal-agent /etc/nginx/sites-enabled/universal-agent
nginx -t
systemctl reload nginx
```

Why:

1. Exposes stable public HTTP(S) endpoint while app stays local-only on internal port.

### 9) Enable HTTPS certificate

What:

```bash
certbot --nginx -d api.clearspringcg.com --redirect -m kevin@clearspringcg.com --agree-tos --non-interactive
```

Why:

1. Composio/webhooks should use HTTPS.
2. Certbot also configured renewal timer automatically.

Result:

1. Valid cert installed for `api.clearspringcg.com`.
2. HTTP redirected to HTTPS.

### 10) Register Composio webhook subscription

What:

1. Ensured `.env` had:
   `UA_GATEWAY_PUBLIC_URL=https://api.clearspringcg.com`
2. Registered subscription:

```bash
cd /opt/universal_agent
PYTHONPATH=src uv run python scripts/register_composio_webhook_subscription.py \
  --webhook-url "https://api.clearspringcg.com/api/v1/hooks/composio"
systemctl restart universal-agent-gateway
```

Why:

1. This tells Composio exactly where to deliver webhook events.
2. Registration writes subscription metadata needed by runtime checks.

Observed result:

1. Subscription created: `ws_yMFCvu8vYsV0`
2. Event type: `composio.trigger.message`

### 11) Validate baseline health and readiness

What:

```bash
curl -i https://api.clearspringcg.com/api/v1/health
cd /opt/universal_agent
PYTHONPATH=src uv run python scripts/check_youtube_ingress_readiness.py
```

Why:

1. Confirms service is reachable externally.
2. Confirms config/env for YouTube ingress are complete.

Expected:

1. Health endpoint returns `HTTP 200`.
2. Readiness JSON shows `"ready": true`.

### 12) Validate manual webhook path

What:

```bash
HOOKS_TOKEN=$(grep '^UA_HOOKS_TOKEN=' /opt/universal_agent/.env | cut -d= -f2-)
curl -i -X POST "https://api.clearspringcg.com/api/v1/hooks/youtube/manual" \
  -H "content-type: application/json" \
  -H "authorization: Bearer ${HOOKS_TOKEN}" \
  -d '{"video_url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","mode":"explainer_only","allow_degraded_transcript_only":true}'
```

Why:

1. Proves non-Composio fallback ingestion is working.

Expected:

1. `HTTP 202` with `{"ok": true, "action": "agent"}`.

### 13) Validate signed Composio webhook path

What:

```bash
cd /opt/universal_agent
set -a
source .env
set +a

export BODY='{"type":"composio.trigger.message","data":{"trigger_slug":"YOUTUBE_NEW_ACTIVITY_TRIGGER","toolkit_slug":"youtube","data":{"video_url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","channel_id":"UC_TEST","title":"Synthetic test"}}}'
export WEBHOOK_ID="$(cat /proc/sys/kernel/random/uuid)"
export WEBHOOK_TS="$(date +%s)"

export SIG="$(python3 - <<'PY'
import os, hmac, hashlib, base64
msg = f"{os.environ['WEBHOOK_ID']}.{os.environ['WEBHOOK_TS']}.{os.environ['BODY']}".encode()
secret = os.environ["COMPOSIO_WEBHOOK_SECRET"].encode()
print(base64.b64encode(hmac.new(secret, msg, hashlib.sha256).digest()).decode())
PY
)"

curl -i -X POST "https://api.clearspringcg.com/api/v1/hooks/composio" \
  -H "content-type: application/json" \
  -H "webhook-id: ${WEBHOOK_ID}" \
  -H "webhook-timestamp: ${WEBHOOK_TS}" \
  -H "webhook-signature: v1,${SIG}" \
  -d "${BODY}"
```

Why:

1. Proves signature verification and Composio mapping path are working.

Expected:

1. `HTTP 202` with `{"ok": true, "action": "agent"}`.

## Problems We Hit And What They Mean

1. `ModuleNotFoundError` during script runs:
   path/env issue, not an app bug.
2. `401 Unauthorized` during first synthetic test:
   caused by unexported vars (`BODY`, `WEBHOOK_ID`, `WEBHOOK_TS`) while generating signature.
3. No immediate live trigger when playlist changed:
   usually Composio trigger-side timing/config/account-scope behavior; UA ingress itself is already validated by synthetic signed tests.

## How To Confirm You Are In The Correct Composio Account/Project

Run on VPS:

```bash
cd /opt/universal_agent
set -a; source .env; set +a
echo "SUB_ID in .env: $COMPOSIO_WEBHOOK_SUBSCRIPTION_ID"
echo "WEBHOOK_URL in .env: $COMPOSIO_WEBHOOK_URL"
curl -sS -H "x-api-key: $COMPOSIO_API_KEY" \
  https://backend.composio.dev/api/v3/webhook_subscriptions
```

You are in the right account/project if all three match:

1. `COMPOSIO_WEBHOOK_SUBSCRIPTION_ID`
2. subscription `id` from API response
3. webhook URL in API response

## Security Ramifications (Important)

What changed:

1. You now expose a public API endpoint on the internet.
2. Bots/scanners will hit `/` and other paths.
3. Secrets are now operational dependencies (`COMPOSIO_WEBHOOK_SECRET`, `UA_HOOKS_TOKEN`, `COMPOSIO_API_KEY`).

Controls now in place:

1. Composio endpoint uses HMAC signature verification.
2. Manual endpoint requires bearer token.
3. TLS enabled.
4. Plaintext hooks token not persisted in `ops_config.json`.

Controls still recommended:

1. Rotate any secret that was ever pasted in terminal/chat/screenshots.
2. Move SSH to key-only auth and disable password auth.
3. Keep firewall tight (80/443/22 only, or tighter).
4. Add alerting on repeated 401/403 and unusual request spikes.

## What This Deployment Gives You

1. Production-style ingress foundation for Composio-triggered automations.
2. Reliable manual ingestion fallback.
3. Reusable pattern for future webhook sources beyond YouTube.

## What It Does Not Guarantee Yet

1. Composio trigger latency for YouTube events (may not be immediate).
2. Trigger behavior without validating each trigger’s exact config and scope in Composio logs.
3. End-to-end playlist-trigger reliability without additional live-event observation.

## Repeat Procedure For A New Domain/Server

1. DNS: point `api.<domain>` to server IP.
2. Install base packages + `uv`.
3. `uv sync` and install missing build deps if needed.
4. Bootstrap hook mappings.
5. Create/start `universal-agent-gateway` systemd service.
6. Configure Nginx reverse proxy.
7. Run Certbot for TLS.
8. Set `UA_GATEWAY_PUBLIC_URL` in `.env`.
9. Register Composio webhook subscription.
10. Restart gateway.
11. Run health + readiness checks.
12. Run manual and synthetic signed webhook tests.
13. Only then begin troubleshooting live trigger behavior.

## Fast Ops Commands (Daily Use)

```bash
systemctl status universal-agent-gateway --no-pager
systemctl restart universal-agent-gateway
journalctl -u universal-agent-gateway -f
curl -i https://api.clearspringcg.com/api/v1/health
cd /opt/universal_agent && PYTHONPATH=src uv run python scripts/check_youtube_ingress_readiness.py
```

## Live Snapshot (As Of 2026-02-11)

1. Domain: `api.clearspringcg.com`
2. Endpoint health: `HTTP 200`
3. Composio subscription ID: `ws_yMFCvu8vYsV0`
4. Manual ingress test: pass (`HTTP 202`)
5. Signed Composio ingress test: pass (`HTTP 202`)

---

## Addendum: Remote App/Web UI + API + Telegram Deployment (2026-02-11, Later Session)

This addendum captures everything completed after the initial webhook-only setup.

### What Was Added

1. Public Web UI domain: `https://app.clearspringcg.com`
2. Dedicated API service on VPS: `universal-agent-api.service` (port `8001`)
3. Dedicated Web UI service on VPS: `universal-agent-webui.service` (port `3000`)
4. Dedicated Telegram bot service on VPS: `universal-agent-telegram.service`
5. Hardened deployment profile: `UA_DEPLOYMENT_PROFILE=vps`

### Why This Matters

Before this addendum, webhook ingress worked, but full remote daily use was not complete.
After this addendum, the system supports:

1. Always-on webhook processing
2. Always-on browser UI access from anywhere
3. Always-on Telegram bot runtime
4. Secure ops endpoints (token required)

### Additional Problems Encountered And Fixes

1. `universal-agent-webui` service failed with `status=127`:
   Cause: `node`/`npm` not installed on VPS.
   Fix: Installed Node 20 and npm, then built Next.js UI with `npm ci && npm run build`.

2. Gateway service instability when run via `uv` under mixed ownership:
   Cause: uv cache permission conflicts from root-owned paths.
   Fix: Standardized service execution to run Python entrypoint as `ua` user with stable service definitions.

3. Duplicate webhook retries returning mixed `202`/`401` in early tests:
   Cause: retry/replay behavior and signature/retry interaction.
   Fix: replay dedupe handling and signature path were validated; ingress now accepts primary delivery and dedupes retries.

### Final Service Topology (Running On VPS)

1. Nginx: public ingress on `80/443`
2. Gateway: `0.0.0.0:8002` (`universal-agent-gateway`)
3. API: `0.0.0.0:8001` (`universal-agent-api`)
4. Web UI: `127.0.0.1:3000` (`universal-agent-webui`)
5. Telegram: polling service (`universal-agent-telegram`)

### Security Hardening Applied

1. `UA_DEPLOYMENT_PROFILE=vps`
2. `UA_ALLOWED_USERS` configured
3. `UA_OPS_TOKEN` enforced for `/api/v1/ops/*`
4. Dashboard auth enabled (`UA_DASHBOARD_AUTH_ENABLED=1`)
5. Dashboard password/session controls configured

### Hardening Validation (Observed)

1. Ops endpoint without token:
   `GET /api/v1/ops/deployment/profile` -> `401 Unauthorized`
2. Same endpoint with `x-ua-ops-token`:
   `GET /api/v1/ops/deployment/profile` -> `200 OK`
3. Returned profile confirms hardened mode:
   `profile=vps`, `allowlist_required=true`, `ops_token_required=true`

### Public Availability Validation (Observed)

1. `https://app.clearspringcg.com` -> `HTTP 200`
2. `https://api.clearspringcg.com/api/v1/health` -> healthy JSON response
3. Telegram service logs show successful polling (`getMe`, `getUpdates`, `200 OK`)

### Operational Outcome

The environment now supports both:

1. Remote-first operation (app UI + API/hooks + Telegram always on VPS)
2. Local development operation (separate local runtime when desired)

These are separate runtime contexts. The production webhook path and public UI are served by VPS.

### Immediate Post-Deployment Security Action

Several credentials appeared in terminal/log/chat during debugging. Rotate all exposed secrets:

1. Telegram bot token
2. Composio API key
3. Composio webhook secret
4. Dashboard password
5. Any token printed to terminal or screenshots

### Cross-Reference

For a full beginner explainer of this second phase, see:

`OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/19_Universal_Agent_VPS_App_API_Telegram_Deployment_Explainer_2026-02-11.md`
