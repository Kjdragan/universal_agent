# 83. Webhook Architecture and Operations Source of Truth (2026-03-06)

## Purpose

This document is the canonical source of truth for webhook usage in Universal Agent.

It explains the shared hook ingress platform, the current production webhook paths, the auth and transform model, the dispatch lifecycle, and how webhook operations differ from trusted in-process dispatch.

## Executive Summary

Universal Agent's webhook platform is centered on:
- `src/universal_agent/hooks_service.py`

This service provides a generalized ingress and dispatch system for external HTTP-triggered events.

In current production, webhook usage is primarily associated with:
- **Composio webhook ingress**
- **manual YouTube webhook ingress**
- **hybrid VPS + local worker YouTube ingest flow**

The shared model is:
1. external POST arrives at `/api/v1/hooks/{subpath}`
2. `HooksService` validates readiness, auth, and mapping
3. an optional transform converts raw payload into a `HookAction`
4. the hook action is queued and dispatched into the agent runtime
5. sessions, notifications, and failure signals are recorded

This document covers the hook platform itself. Topic-specific behavior, especially YouTube ingest, is cross-referenced where relevant.

## Canonical Webhook Entry Points

Primary HTTP routes in the gateway:
- `GET /api/v1/hooks/readyz`
- `POST /api/v1/hooks/{subpath}`

### `GET /api/v1/hooks/readyz`

Purpose:
- no-auth readiness probe for webhook infrastructure
- preferred probe for operator health checks

This is the canonical way to check whether hook ingress is actually available without supplying auth.

### `POST /api/v1/hooks/{subpath}`

Purpose:
- authenticated or policy-controlled hook ingress for mapped webhook sources

Examples of active subpaths include:
- `composio`
- `youtube/manual`

## Core Implementation

Primary implementation:
- `src/universal_agent/hooks_service.py`

The hook system defines:
- hook config models
- auth strategies
- transform loading
- action building
- agent dispatch queueing
- retry/defer behavior for YouTube ingest integration
- notification emission for failures and queue overflow

## High-Level Architecture

### 1. External ingress

Webhook HTTP traffic enters through the gateway and is handed to the hooks service.

### 2. Mapping resolution

Mappings define:
- request match conditions
- auth behavior
- optional transform module
- target action type
- route target (`to`)
- model/thinking/timeout overrides when needed

### 3. Authentication

Current auth strategies include:
- `token`
- `composio_hmac`
- `none`

### 4. Transform step

Transform modules in `webhook_transforms/` can convert raw payloads into a normalized `HookAction`.

Current known transforms include:
- `composio_youtube_transform.py`
- `manual_youtube_transform.py`
- `agentmail_transform.py`

### 5. Action dispatch

Resolved actions are dispatched into the runtime as agent or wake actions.

For agent actions, the hooks service:
- normalizes session context
- enforces queue/concurrency limits
- emits notifications on overflow or error
- routes to the requested agent/session lane

## Auth Model

## Token Auth

Default behavior:
- checks the configured hooks token for incoming HTTP webhook requests
- used for manual or bearer-protected hook paths

Typical env surface:
- `UA_HOOKS_ENABLED`
- `UA_HOOKS_TOKEN`

## Composio HMAC Auth

Composio requests can be verified using HMAC-based validation.

Current behavior includes:
- signature extraction from request headers
- timestamp tolerance validation
- replay protection using stored webhook ids
- secret loading from env, defaulting to `COMPOSIO_WEBHOOK_SECRET`

This is the authoritative trust path for public Composio-triggered hook ingress.

## Trusted Internal Dispatch Is Different

Webhook ingress should not be confused with trusted in-process dispatch.

Separate path:
- `HooksService.dispatch_internal_action(...)`

That path is used by trusted in-process callers such as the AgentMail service and bypasses external webhook auth and mapping resolution.

This distinction matters:
- **webhook path** = external HTTP ingress with auth/mapping/transform pipeline
- **internal dispatch path** = in-process trusted action injection

## Transform Layer

Transform resolution is file-based and dynamic.

Implementation behavior:
- resolves transform modules relative to ops config / transform directory
- loads and caches the transform function
- merges transform output into a base `HookAction`
- allows transform to return `None` to skip the event

This makes the webhook system extensible without hardcoding every source in the gateway.

## Dispatch Queueing and Concurrency

The hooks service intentionally constrains agent dispatch concurrency.

Key runtime controls include:
- `UA_HOOKS_AGENT_DISPATCH_CONCURRENCY`
- `UA_HOOKS_AGENT_DISPATCH_QUEUE_LIMIT`

Why:
- bursty webhook traffic can overwhelm memory or runtime lanes if left unconstrained
- YouTube and other heavy paths may involve long-running follow-up work

Current behavior includes:
- pending-count admission check
- bounded semaphore-based dispatch gate
- queue overflow notification emission
- queue wait timing metadata when delay is non-trivial

## Current Production Webhook Paths

## 1. Composio Webhook Ingress

Primary route:
- `POST /api/v1/hooks/composio`

Operational helpers:
- `scripts/register_composio_webhook_subscription.py`

This path is used for Composio-triggered events such as YouTube-related triggers.

Associated runbooks/reference docs:
- `03_Operations/18_Hostinger_VPS_Composio_Webhook_Deployment_Runbook_2026-02-11.md`
- `03_Operations/42_Hybrid_Local_VPS_Webhook_Operations_Source_Of_Truth_2026-02-18.md`
- `03_Operations/45_YouTube_Webhook_Robustness_And_Gemini_Video_Analysis_Implementation_Ticket_2026-02-19.md`

## 2. Manual YouTube Hook

Primary route:
- `POST /api/v1/hooks/youtube/manual`

Purpose:
- operator or script-triggered manual ingestion entry point
- useful for testing, recovery, or explicit video processing requests

This route still goes through the hook system and should be treated as part of the canonical webhook platform.

## 3. Hybrid Local+VPS YouTube Path

This is a specialized operational webhook path built on top of the generic hook platform.

Canonical flow:
1. webhook arrives on VPS
2. hooks service resolves the YouTube action
3. hooks service may call a local ingest endpoint before dispatching agent work
4. ingest evidence is written into hook session artifacts
5. downstream tutorial/explainer work proceeds on VPS

This flow is documented in detail in:
- `03_Operations/42_Hybrid_Local_VPS_Webhook_Operations_Source_Of_Truth_2026-02-18.md`

## Webhook-Related Environment Variables

Shared hook platform env surface in `.env.sample` includes:
- `UA_HOOKS_ENABLED`
- `UA_HOOKS_TOKEN`
- `UA_HOOKS_YOUTUBE_INGEST_MODE`
- `UA_HOOKS_YOUTUBE_INGEST_URL`
- `UA_HOOKS_YOUTUBE_INGEST_TOKEN`
- `UA_HOOKS_YOUTUBE_INGEST_TIMEOUT_SECONDS`
- `UA_HOOKS_YOUTUBE_INGEST_RETRY_ATTEMPTS`
- `UA_HOOKS_YOUTUBE_INGEST_RETRY_DELAY_SECONDS`
- `UA_HOOKS_YOUTUBE_INGEST_RETRY_MAX_DELAY_SECONDS`
- `UA_HOOKS_YOUTUBE_INGEST_RETRY_JITTER_SECONDS`
- `UA_HOOKS_YOUTUBE_INGEST_MIN_CHARS`
- `UA_HOOKS_YOUTUBE_INGEST_COOLDOWN_SECONDS`
- `UA_HOOKS_YOUTUBE_INGEST_INFLIGHT_TTL_SECONDS`
- `UA_HOOKS_YOUTUBE_INGEST_FAIL_OPEN`
- `UA_HOOKS_DEFAULT_TIMEOUT_SECONDS`
- `UA_HOOKS_YOUTUBE_TIMEOUT_SECONDS`
- `UA_HOOKS_YOUTUBE_IDLE_TIMEOUT_SECONDS`
- `UA_HOOKS_SYNC_READY_MARKER_ENABLED`
- `UA_HOOKS_STARTUP_RECOVERY_ENABLED`

Composio-specific secret/config surface includes:
- `COMPOSIO_WEBHOOK_SECRET`
- `COMPOSIO_WEBHOOK_URL`
- `COMPOSIO_WEBHOOK_SUBSCRIPTION_ID`

## Operator Health Signals

Healthy webhook platform signals:
- `GET /api/v1/hooks/readyz` returns ready and `hooks_enabled=true`
- inbound hook requests return accepted status instead of auth/config failure
- hook session creation occurs for dispatched actions
- gateway logs show hook action dispatch rather than repeated rejection/defer loops

Potential failure signatures:
- `404 Hooks disabled`
- `401 Unauthorized`
- HMAC verification failures
- dispatch queue overflow notifications
- repeated ingest deferrals or failures in YouTube-associated runs

## Security Model

The current security posture is:
- authenticate external webhook traffic
- prefer HMAC verification for Composio when available
- prevent replay via webhook id tracking
- keep queue concurrency intentionally tight
- treat transforms as controlled local code, not untrusted remote logic
- separate external webhook auth path from trusted in-process dispatch path

## Current Gaps and Follow-Up Items

1. **AgentMail webhook parity**
   - `webhook_transforms/agentmail_transform.py` exists
   - current production email ingress is WebSocket-first, not webhook-first
   - if AgentMail webhook ingress is reactivated, it should reach parity with the WebSocket email path, including reply extraction behavior

2. **Generic vs topic-specific documentation overlap**
   - several older webhook docs are YouTube-heavy
   - this canonical doc should be the generic webhook first-stop, with YouTube-specific docs used as specialized references

3. **Observability consolidation**
   - a future improvement could document or expose hook queue metrics more directly in ops UI surfaces

## Source Files That Define Current Truth

Primary implementation:
- `src/universal_agent/hooks_service.py`
- `src/universal_agent/gateway_server.py`
- `webhook_transforms/composio_youtube_transform.py`
- `webhook_transforms/manual_youtube_transform.py`
- `webhook_transforms/agentmail_transform.py`
- `scripts/register_composio_webhook_subscription.py`

Primary operational references:
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/18_Hostinger_VPS_Composio_Webhook_Deployment_Runbook_2026-02-11.md`
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/42_Hybrid_Local_VPS_Webhook_Operations_Source_Of_Truth_2026-02-18.md`
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/45_YouTube_Webhook_Robustness_And_Gemini_Video_Analysis_Implementation_Ticket_2026-02-19.md`

## Bottom Line

The canonical webhook platform in Universal Agent is the `HooksService`.

The production webhook story is:
- **external HTTP ingress enters through `/api/v1/hooks/*`**
- **auth and mapping are enforced in `HooksService`**
- **transforms normalize raw payloads into hook actions**
- **bounded dispatch sends work into the agent runtime**
- **trusted in-process dispatch is a separate path and should not be conflated with public webhook ingress**
