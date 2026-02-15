# 19. Universal Agent VPS App/API/Telegram Deployment Explainer (2026-02-14 Update)

## Purpose

This document explains the production deployment on the Hostinger VPS.
**Status Update (2026-02-14):**

- **Management Access**: Now strictly via **Tailscale VPN** (`100.106.113.93`). Public SSH (port 22) is BLOCKED.
- **Public Access**: Web UI (`app.clearspringcg.com`) and Webhooks (`api.clearspringcg.com`) remain PUBLIC.

This is intended to be the source of truth for:

1. Public Web UI access (`app.clearspringcg.com`)
2. Public API + webhook ingress (`api.clearspringcg.com`)
3. Composio trigger ingestion for YouTube automation
4. Telegram bot always-on runtime

---

## Executive Summary

By the end of this deployment, the system was successfully running as four persistent services on VPS:

1. `universal-agent-gateway` (port `8002`)
2. `universal-agent-api` (port `8001`)
3. `universal-agent-webui` (port `3000`, served publicly via Nginx at `app.clearspringcg.com`)
4. `universal-agent-telegram` (long polling Telegram runtime)

Public routing and TLS are active:

1. `https://api.clearspringcg.com` -> Gateway/API ingress
2. `https://app.clearspringcg.com` -> Web UI

Security hardening was also applied:

1. Deployment profile set to `vps`
2. Ops token protection enforced (`401` without token, `200` with token)
3. User allowlist enabled
4. Dashboard auth enabled

---

## What Was Built (Architecture)

### DNS and Public Entry Points

1. Domain/DNS is managed in GoDaddy.
2. Subdomains point to Hostinger VPS public IP.
3. Nginx handles TLS termination and reverse proxying.

### Runtime Topology on VPS

1. Nginx listens on `80/443`.
2. Web UI (Next.js) runs locally on `127.0.0.1:3000`.
3. API server (FastAPI) runs on `0.0.0.0:8001`.
4. Gateway server (FastAPI + execution + hooks) runs on `0.0.0.0:8002`.
5. Telegram service runs as a separate Python process and uses the gateway internally.

### Service Manager

All major components now run under systemd for persistence across reboots.

Service names:

1. `universal-agent-gateway.service`
2. `universal-agent-api.service`
3. `universal-agent-webui.service`
4. `universal-agent-telegram.service`

---

## What We Changed and Why

### 1) Gateway moved to production-style service runtime

We configured and stabilized a systemd service for gateway so webhook ingress is always available (not dependent on a local terminal).

Why: webhooks require a continuously reachable public endpoint.

### 2) Composio webhook subscription registered to public endpoint

Webhook target was set to:
`https://api.clearspringcg.com/api/v1/hooks/composio`

Why: Composio must deliver trigger payloads to a public HTTPS URL.

### 3) Signature handling + replay dedupe behavior validated

Observed behavior in logs:

1. First POST accepted (`202`)
2. Retry deliveries handled as replay/dedupe

Why: webhook providers retry on timing/network conditions; dedupe avoids duplicate job execution.

### 4) API service deployed as its own systemd unit

`universal-agent-api.service` was created to run `universal_agent.api.server` on port `8001`.

Why: Web UI depends on API availability.

### 5) Web UI deployment completed (Node/Next install + build)

Initially failed due to missing `node`/`npm` and missing `next` runtime. Then fixed by installing Node 20, running `npm ci`, and `npm run build`.

Why: Next.js UI requires node runtime and production build artifacts.

### 6) Nginx + TLS for app subdomain

Nginx site configured for `app.clearspringcg.com` proxying to `127.0.0.1:3000`, then cert issued by certbot.

Why: public secure access to UI from anywhere.

### 7) Telegram bot deployed as always-on systemd service

`universal-agent-telegram.service` created and confirmed running with successful Telegram API polling requests.

Why: bot no longer depends on local machine uptime.

### 8) VPS security posture switched from local defaults

Confirmed via endpoint:
`/api/v1/ops/deployment/profile`

Result:

1. profile: `vps`
2. `allowlist_required: true`
3. `ops_token_required: true`

Why: internet-exposed runtime must not remain in local/open profile.

---

## Validation Results (What is Confirmed Working)

### Ops auth behavior

1. Without token: `401 Unauthorized`
2. With `x-ua-ops-token`: `200 OK`

This confirms ops protection is active.

### Service status

All four services reported `active (running)`:

1. gateway
2. api
3. webui
4. telegram

### Public endpoint checks

1. `https://app.clearspringcg.com` returned `200`
2. `https://api.clearspringcg.com/api/v1/health` returned healthy JSON

### Telegram runtime

Logs show successful `getMe`, `deleteWebhook`, and repeated `getUpdates` responses (`200 OK`).

### Composio ingress

Ingress requests observed and accepted; replay requests deduped as expected.

---

## Important Security Notes

### 1) Secrets were exposed during troubleshooting

Several secrets/tokens were printed in terminal output during setup.

Action required:

1. Rotate Telegram bot token
2. Rotate Composio API key
3. Rotate Composio webhook secret
4. Rotate dashboard password
5. Rotate any token shown in logs/chat/screenshots

### 2) Keep dashboard auth enabled

Required env controls:

1. `UA_DASHBOARD_AUTH_ENABLED=1`
2. `UA_DASHBOARD_PASSWORD=<strong secret>`
3. `UA_DASHBOARD_SESSION_SECRET=<strong secret>`

### 3) Keep ops token controls enabled

Required env controls:

1. `UA_OPS_TOKEN=<strong secret>`
2. `UA_DASHBOARD_OPS_TOKEN=<same or dedicated strong secret>`

### 4) Keep allowlist active

`UA_ALLOWED_USERS` should remain defined for intended operators.

---

## Local vs VPS: Operational Model

### VPS runtime (always-on, production-like)

1. Handles public webhooks
2. Hosts web UI and API
3. Runs Telegram bot continuously
4. Survives local machine shutdown

### Local runtime (development)

1. Optional for coding/testing
2. Separate process space and session state from VPS
3. Should not run duplicate Telegram bot with same token at same time

---

## Daily Operations Cheat Sheet

Run on VPS (Must be on Tailscale -> `ssh root@100.106.113.93`):

### Check service health

```bash
systemctl status universal-agent-gateway --no-pager
systemctl status universal-agent-api --no-pager
systemctl status universal-agent-webui --no-pager
systemctl status universal-agent-telegram --no-pager
```

### Restart all services

```bash
systemctl restart universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram
```

### Tail logs

```bash
journalctl -u universal-agent-gateway -f
journalctl -u universal-agent-api -f
journalctl -u universal-agent-webui -f
journalctl -u universal-agent-telegram -f
```

### Validate secure ops endpoint

```bash
cd /opt/universal_agent
set -a; source .env; set +a
curl -i https://api.clearspringcg.com/api/v1/ops/deployment/profile
curl -i -H "x-ua-ops-token: $UA_OPS_TOKEN" https://api.clearspringcg.com/api/v1/ops/deployment/profile
```

Expected:

1. first call -> `401`
2. second call -> `200`

---

## Known Limitations / Follow-ups

1. Root company site (`clearspringcg.com`) is intentionally not finalized in this phase.
2. Web UI and API are deployed for app usage; company landing page can be added later as separate Nginx site.
3. Security tightening can still be improved with:
   1. stricter firewall rules
   2. optional VPN/IP restriction for sensitive routes
   3. periodic key rotation policy

---

## Repeat This Setup in Future (High-Level Sequence)

1. Point DNS subdomains to VPS IP.
2. Install runtime dependencies on VPS (Python/uv, Node for web-ui, nginx, certbot).
3. Deploy repo to `/opt/universal_agent`.
4. Configure `.env` with production-safe values.
5. Create/enable systemd services (gateway/api/webui/telegram).
6. Configure Nginx reverse proxies for `api.` and `app.`.
7. Issue/renew TLS certs with certbot.
8. Validate health endpoints and auth behavior.
9. Test external webhook delivery and replay handling.
10. Rotate any exposed secrets before go-live.

---

## File/Path References

1. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/19_Universal_Agent_VPS_App_API_Telegram_Deployment_Explainer_2026-02-11.md`
2. `/opt/universal_agent/.env` (VPS runtime env)
3. `/etc/systemd/system/universal-agent-gateway.service`
4. `/etc/systemd/system/universal-agent-api.service`
5. `/etc/systemd/system/universal-agent-webui.service`
6. `/etc/systemd/system/universal-agent-telegram.service`
7. `/etc/nginx/sites-available/universal-agent`
8. `/etc/nginx/sites-available/universal-agent-app`

---

## Final Status (as of 2026-02-11)

Deployment objective for remote operation has been achieved:

1. Web UI accessible globally at `app.clearspringcg.com`
2. API/hooks active at `api.clearspringcg.com`
3. Telegram bot running continuously on VPS
4. Security posture moved from local defaults to VPS profile with token-gated ops endpoints and allowlist
