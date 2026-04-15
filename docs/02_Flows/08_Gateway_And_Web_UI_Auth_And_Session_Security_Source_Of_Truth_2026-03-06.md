# 08. Gateway and Web UI Auth and Session Security Source of Truth (2026-03-06)

## Purpose

This document is the canonical source of truth for current authentication, dashboard access control, and session ownership enforcement across the Web UI, API server, and gateway streaming surfaces.

It explains what is implemented now, which secrets and cookies control access, how owner-based session visibility works, where internal-service bypasses exist, and which behaviors are convenience mechanisms versus real security boundaries.

## Executive Summary

The current auth/session model has four layers:

1. **Dashboard login and cookie session issuance in Next.js**
2. **Cookie validation and owner resolution in the API server**
3. **Internal token bypass for trusted service-to-service calls**
4. **Session ownership enforcement against gateway session metadata**

This is not a full RBAC platform.

The implemented model is primarily:
- dashboard authentication by password or owner-specific password hash
- HMAC-signed dashboard session cookie
- owner-lane filtering for session access
- privileged internal bypass via `UA_INTERNAL_API_TOKEN` or `UA_OPS_TOKEN`

## Current Production Architecture

## 1. Web UI Login and Cookie Session

Primary implementation:
- `web-ui/lib/dashboardAuth.ts`
- `web-ui/app/api/dashboard/auth/login/route.ts`
- `web-ui/app/api/dashboard/auth/session/route.ts`
- `web-ui/app/api/dashboard/auth/logout/route.ts`
- `web-ui/app/dashboard/layout.tsx`
- `web-ui/app/page.tsx`

The Web UI issues an HTTP-only cookie named:
- `ua_dashboard_auth`

The cookie payload is:
- base64url-encoded JSON payload
- plus HMAC SHA-256 signature

Payload fields currently include:
- `owner_id`
- `exp`
- `roles`

### Auth Requirement Rules

Dashboard auth is considered required when either of these is true:
- `UA_DASHBOARD_AUTH_ENABLED` is explicitly truthy
- dashboard owners are configured
- `UA_DASHBOARD_PASSWORD` is configured

If auth is not required, the dashboard behaves as authenticated for the default owner lane.

### Credential Sources

Current supported credential sources:
- `UA_DASHBOARD_OWNERS_JSON`
- `UA_DASHBOARD_OWNERS_FILE`
- `UA_DASHBOARD_PASSWORD`

Owner records support:
- `owner_id`
- `password_hash`
- `active`
- `roles`

Owner-specific passwords use PBKDF2-SHA256 hashes.

If owner records are not configured, the fallback is a single shared dashboard password.

### Session Secret Resolution

The session-signing secret is resolved in this order:
- `UA_DASHBOARD_SESSION_SECRET`
- `UA_OPS_TOKEN`
- `UA_DASHBOARD_PASSWORD`
- hardcoded dev fallback `ua-dashboard-dev-secret`

That final fallback is convenient for development but should be treated as a security gap in hardened deployments.

## 2. Dashboard-to-Gateway Proxy Layer

Primary implementation:
- `web-ui/app/api/dashboard/gateway/[...path]/route.ts`

The dashboard does not call the gateway directly from the browser for these proxied routes.

Instead, the Next.js route:
- validates the dashboard cookie session
- forwards requests upstream to the configured gateway base URL
- injects owner context with `x-ua-dashboard-owner`
- injects privileged gateway auth using `UA_DASHBOARD_OPS_TOKEN`, `UA_OPS_TOKEN`, or `NEXT_PUBLIC_UA_OPS_TOKEN`

### Upstream URL Resolution

The proxy resolves the gateway URL from:
- `UA_DASHBOARD_GATEWAY_URL`
- `NEXT_PUBLIC_GATEWAY_URL`
- `UA_GATEWAY_URL`
- fallback `http://localhost:8002`

### Optional Owner Filter Injection

If `UA_DASHBOARD_ENFORCE_OWNER_FILTER` is enabled, the proxy automatically adds `owner=<owner_id>` to selected ops endpoints such as:
- `/api/v1/ops/sessions`
- `/api/v1/ops/calendar/events`

This is a convenience control, not the primary authorization boundary.

## 3. API Server Dashboard Auth Enforcement

Primary implementation:
- `src/universal_agent/api/server.py`

The API server mirrors the dashboard cookie logic so it can enforce the same auth state on:
- REST session endpoints
- file browsing endpoints
- artifact browsing endpoints
- WebSocket endpoints

Current auth middleware behavior:
- authenticates dashboard requests once per request
- stores auth on `request.state.dashboard_auth`
- returns `401` with `Dashboard login required.` if auth is required but missing

### Internal Trusted-Caller Bypass

Both HTTP and WebSocket auth paths support a trusted internal token bypass.

Recognized header styles:
- `Authorization: Bearer <token>`
- `x-ua-internal-token`
- `x-ua-ops-token`

Internal token resolution order:
- `UA_INTERNAL_API_TOKEN`
- `UA_OPS_TOKEN`

If the presented header token matches the internal token, the request is treated as authenticated regardless of dashboard cookie state.

This is an intentional privileged path and should be treated as admin-equivalent access.

## 4. Session Ownership Enforcement

Primary implementation:
- `src/universal_agent/api/server.py`
- gateway lookup via `UA_GATEWAY_URL`

The real session-access boundary is not only “is the dashboard logged in?”

It is also:
- “does the authenticated owner match the session owner?”

### Enforcement Flow

For session reads, session stream proxying, and session-attached WebSocket use, the API server:
1. resolves the authenticated owner
2. calls the gateway session endpoint
3. compares the requested owner with the session owner
4. allows or blocks based on explicit rules

### Allow Rules

A session is allowed when:
- the session owner matches the authenticated owner
- the session is system-owned and visible from the dashboard owner lane
- the session id starts with `session_hook_`, `run_session_hook_`, or `cron_` and the authenticated owner is the primary dashboard owner

### Deny Rules

Access is denied when:
- a different non-system owner owns the session
- auth is required and the gateway cannot verify ownership

This is the main protection against one dashboard owner browsing another owner’s session lane.

## Current WebSocket Security Model

## 1. `/ws/agent`

Primary implementation:
- `src/universal_agent/api/server.py`

Behavior:
- requires dashboard auth when enabled
- enforces owner match if resuming an existing session
- creates new sessions under the authenticated owner id
- resumes existing session only after ownership check
- is the intended browser-facing WebSocket entrypoint for the web UI

Important current behavior:
- if `UA_GATEWAY_URL` is configured, the API server passively subscribes to gateway session broadcasts so background/system events appear in the UI
- duplicate active-query events are suppressed while a foreground query is in flight
- browser traffic should terminate here or at the API server's `/api/v1/sessions/{session_id}/stream`, not directly on the gateway's token-gated WebSocket

## 2. `/api/v1/sessions/{session_id}/stream`

Primary implementation:
- `src/universal_agent/api/server.py`
- upstream gateway stream in `src/universal_agent/gateway_server.py`

Behavior:
- validates dashboard auth unless internal token bypass is present
- enforces owner check before proxying
- connects to the gateway stream endpoint and forwards raw stream messages
- is the browser-facing direct-session attach path when the UI needs a specific existing session

Current close reasons include:
- `4401` for dashboard login required or unauthorized
- `4403` for owner mismatch
- `1011` for gateway configuration or upstream availability failures

## Gateway-Side Session API Security

Primary implementation:
- `src/universal_agent/gateway_server.py`

The gateway maintains a separate token-protected session API surface.

Current session API auth requirement:
- enabled automatically on `vps`
- also enabled whenever `SESSION_API_TOKEN` is present

`SESSION_API_TOKEN` resolves from:
- `UA_INTERNAL_API_TOKEN`
- else `UA_OPS_TOKEN`

Protected gateway surfaces include:
- session stream WebSocket auth
- resume endpoints
- delete endpoints
- other session-control paths

This means the API server and dashboard proxy are expected to operate as trusted callers into the gateway.

## Key Invariants

The current system assumes all of the following:

1. **Dashboard owner is the effective user identity**
   - new sessions created through the dashboard are created under the authenticated owner

2. **Internal tokens are privileged**
   - any holder of the internal token can bypass normal dashboard cookie auth

3. **Gateway ownership is authoritative**
   - the API server relies on the gateway session record to decide whether an owner may resume or inspect a session

4. **System sessions are intentionally observable**
   - webhook, cron, worker, and VP-oriented sessions may be exposed to the main dashboard owner lane

## Canonical Environment Controls

Dashboard login/session controls:
- `UA_DASHBOARD_AUTH_ENABLED`
- `UA_DASHBOARD_PASSWORD`
- `UA_DASHBOARD_OWNER_ID`
- `UA_DASHBOARD_OWNERS_JSON`
- `UA_DASHBOARD_OWNERS_FILE`
- `UA_DASHBOARD_SESSION_SECRET`
- `UA_DASHBOARD_SESSION_TTL_SECONDS`

Dashboard proxy controls:
- `UA_DASHBOARD_GATEWAY_URL`
- `UA_DASHBOARD_OPS_TOKEN`
- `UA_DASHBOARD_ENFORCE_OWNER_FILTER`
- `NEXT_PUBLIC_GATEWAY_URL`
- `NEXT_PUBLIC_UA_OPS_TOKEN`

Internal gateway/API auth controls:
- `UA_INTERNAL_API_TOKEN`
- `UA_OPS_TOKEN`
- `UA_GATEWAY_URL`
- `UA_OPS_JWT_SECRET`

## What Is Actually Implemented Today

### Implemented and Current

- HMAC-signed dashboard session cookie
- owner-aware login and owner resolution
- PBKDF2-based owner password verification
- API-side cookie validation for REST and WebSocket flows
- session ownership enforcement through gateway lookups
- internal token bypass for trusted service paths

### Present but Not Full RBAC

- roles are included in dashboard session payloads
- owner records carry `roles`
- current code does not make roles the primary enforcement mechanism for normal session access

### Important Security Caveats

- fallback signing secret exists for development
- `NEXT_PUBLIC_UA_OPS_TOKEN` is an inherently high-risk option if used outside tightly controlled development contexts
- owner filter injection is optional and not itself sufficient authorization
- internal token holders effectively bypass normal dashboard login checks

## Current Gaps and Follow-Up Items

1. **Dev-secret fallback remains available**
   - hardened deployments should avoid falling back to `ua-dashboard-dev-secret`

2. **Role model is not fully operationalized**
   - `roles` are minted into sessions but are not the main policy surface for dashboard authorization

3. **Internal-token blast radius is broad**
   - current trusted-service bypass is practical, but it is admin-grade access and should be documented as such everywhere it is reused

4. **Owner semantics are partly special-cased**
   - system sessions and hook sessions have explicit exceptions, which is useful operationally but should remain intentional and documented

## Source Files That Define Current Truth

Primary implementation:
- `web-ui/lib/dashboardAuth.ts`
- `web-ui/app/api/dashboard/auth/login/route.ts`
- `web-ui/app/api/dashboard/auth/session/route.ts`
- `web-ui/app/api/dashboard/auth/logout/route.ts`
- `web-ui/app/api/dashboard/gateway/[...path]/route.ts`
- `src/universal_agent/api/server.py`
- `src/universal_agent/gateway_server.py`

Relevant consumers:
- `web-ui/app/dashboard/layout.tsx`
- `web-ui/app/page.tsx`
- `web-ui/lib/sessionDirectory.ts`

## Bottom Line

The canonical current security model for the Web UI and session flows is:
- **cookie-based dashboard auth**
- **owner-lane session authorization**
- **trusted internal token bypass for service paths**
- **gateway-owned session metadata as the authority for resume/inspect decisions**

It is effective for current operations, but it should be understood as a pragmatic owner-plus-internal-token model, not a fully generalized RBAC security framework.
