# 21. Web Chat and Session Security Hardening Explainer (2026-02-11)

## Purpose
This document explains, in beginner-friendly language, the security controls added on February 11, 2026 to protect:
1. Web chat access (`app.clearspringcg.com`)
2. Session creation and streaming APIs
3. Session/file isolation between owners

It is focused on practical understanding:
1. What was exposed before
2. What is protected now
3. What each control blocks
4. How to operate it safely

---

## Executive Summary
Before this hardening, a user could reach the public app and open a live chat websocket without completing dashboard login.

After this hardening:
1. Unauthenticated users are blocked from chat/session/file APIs.
2. Unauthenticated websocket connections are rejected.
3. Authenticated users are limited to their own sessions/files.
4. API-to-gateway session traffic now requires an internal token.
5. Gateway session endpoints are no longer publicly open in VPS mode.
6. Dashboard auth now supports multi-owner credentials (PBKDF2 hashed passwords).

---

## What This Protects (Beginner View)

### 1) Prevents strangers from using your chat runtime
Without auth, a public visitor could consume your compute budget and potentially inspect outputs.

Now:
1. `POST /api/sessions` returns `401` unless dashboard-authenticated.
2. `/ws/agent` websocket handshake is rejected unless authenticated.

### 2) Prevents one owner from reading another owner's sessions/files
A logged-in user should not be able to inspect someone else's workspace data.

Now:
1. Session/file routes perform owner checks.
2. Owner mismatch returns `403`.

### 3) Prevents direct public abuse of gateway session endpoints
The gateway is your execution engine. Public unauthenticated access is high risk.

Now:
1. `/api/v1/sessions*` and `/api/v1/sessions/{id}/stream` require internal token auth.
2. Unauthenticated calls get `401` or websocket `4401` close.

### 4) Prepares for multi-owner operations
Single shared password is hard to operate safely at team scale.

Now:
1. Owner records are supported with per-owner hashed credentials.
2. You can add owners later without redesigning auth.

---

## Security Layers Added

### Layer A: Dashboard-Authenticated App API Access
Location:
1. `src/universal_agent/api/server.py`

Behavior:
1. API middleware checks dashboard session token on `/api/*`.
2. Only `/api/health` remains open.
3. Unauthenticated API requests return `401` with `Dashboard login required`.

Protection:
1. Blocks anonymous API access from public internet.

### Layer B: Dashboard-Authenticated WebSocket Access
Location:
1. `src/universal_agent/api/server.py`

Behavior:
1. `/ws/agent` validates dashboard session cookie (or approved internal token).
2. Unauthenticated websocket requests are rejected.

Protection:
1. Blocks anonymous real-time chat execution.

### Layer C: Owner-Bound Session and File Authorization
Location:
1. `src/universal_agent/api/server.py`

Behavior:
1. New sessions are created under authenticated owner identity.
2. Session/file access verifies owner before allowing read/attach.
3. Owner mismatch returns `403`.

Protection:
1. Prevents cross-owner data access.

### Layer D: Internal Token Between API and Gateway
Location:
1. `src/universal_agent/api/gateway_bridge.py`
2. `src/universal_agent/gateway_server.py`

Behavior:
1. API bridge adds internal auth headers to gateway REST and websocket calls.
2. Gateway validates those headers for session endpoints.

Protection:
1. Prevents direct public use of gateway session interfaces.

### Layer E: VPS Session-Create Identity Hardening
Location:
1. `src/universal_agent/gateway_server.py`

Behavior:
1. In `vps` profile, session creation requires explicit `user_id`.
2. Implicit fallback identity is not accepted for external session creation.

Protection:
1. Reduces ambiguous identity assignment and accidental auth bypass paths.

### Layer F: Multi-Owner Credential Model
Location:
1. `web-ui/lib/dashboardAuth.ts`
2. `web-ui/app/api/dashboard/auth/login/route.ts`
3. `scripts/manage_dashboard_owners.py`

Behavior:
1. Supports owner records with PBKDF2-SHA256 password hashes.
2. Supports owner active/inactive state and role list.
3. Supports loading from JSON file or env JSON.

Protection:
1. Enables safer scaling beyond one shared password.

---

## Public vs Protected Endpoints (Current Intent)

### Public by design
1. `https://app.clearspringcg.com/api/health`
2. Gateway webhook ingress endpoints on `api.clearspringcg.com` (for YouTube/Composio trigger delivery)
3. Gateway health endpoint

### Protected
1. App chat/session/file endpoints (`/api/sessions`, `/api/files`, etc.)
2. App websocket chat stream (`/ws/agent`)
3. Gateway session endpoints (`/api/v1/sessions*`, stream endpoint)
4. Ops endpoints (already token-protected)

---

## Multi-Owner Operations

### Credential storage options
1. File: `config/dashboard_owners.json`
2. Env JSON: `UA_DASHBOARD_OWNERS_JSON`
3. Optional file override: `UA_DASHBOARD_OWNERS_FILE`

### Owner management script
Path:
1. `scripts/manage_dashboard_owners.py`

Examples:
```bash
# list owners
python3 scripts/manage_dashboard_owners.py list

# add/update owner (default role: admin)
python3 scripts/manage_dashboard_owners.py set \
  --owner-id owner_primary \
  --password 'CHANGE_ME_STRONG_PASSWORD'

# remove owner
python3 scripts/manage_dashboard_owners.py remove --owner-id owner_primary
```

Notes:
1. Passwords are stored as PBKDF2 hashes, not plaintext.
2. Keep the owners file out of public repositories.
3. Restrict read permissions on the file on VPS.

---

## Verification Checklist (Copy/Paste)

From VPS:
```bash
cd /opt/universal_agent
set -a; source .env; set +a

# 1) Unauth app session create should fail
curl -i -X POST https://app.clearspringcg.com/api/sessions \
  -H "content-type: application/json" \
  -d '{}'

# 2) Dashboard auth session endpoint should return 401 when not logged in
curl -i https://app.clearspringcg.com/api/dashboard/auth/session

# 3) Gateway sessions should fail without token
curl -i https://api.clearspringcg.com/api/v1/sessions

# 4) Gateway sessions should succeed with token
curl -i -H "x-ua-ops-token: $UA_OPS_TOKEN" https://api.clearspringcg.com/api/v1/sessions
```

Expected:
1. Unauth app session create -> `401`.
2. Unauth dashboard auth session -> `401`.
3. Unauth gateway sessions -> `401`.
4. Authenticated gateway sessions -> `200`.

---

## Operational Risks to Remember
1. If `UA_OPS_TOKEN` (or `UA_INTERNAL_API_TOKEN`) leaks, attacker may reach protected internal gateway session endpoints.
2. If dashboard owner credentials are weak/reused, account takeover risk remains.
3. Existing legacy sessions with unknown/mismatched owner metadata may be intentionally inaccessible after owner isolation hardening.
4. Any future new endpoint should be reviewed for auth scope before deployment.

---

## Recommended Next Security Steps
1. Rotate `UA_OPS_TOKEN` and dashboard passwords if they were ever exposed in logs/screenshots.
2. Create a second owner account for tested multi-owner flow before production expansion.
3. Add automated regression tests for:
   1. unauthenticated `401` on app session/file endpoints
   2. unauthenticated websocket rejection
   3. cross-owner `403` isolation
4. Add periodic audit command to detect any endpoint accidentally left unauthenticated.

