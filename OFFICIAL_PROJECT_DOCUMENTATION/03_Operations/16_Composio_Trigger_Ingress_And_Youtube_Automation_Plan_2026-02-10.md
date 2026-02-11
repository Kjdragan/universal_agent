# Composio Trigger Ingress And YouTube Automation Plan

**Date:** 2026-02-10  
**Status:** Proposed (Implementation Plan)

## Objective

Design a production-safe trigger architecture that supports:

1. Composio trigger webhooks as the primary inbound event source.
2. Non-Composio webhook/fallback sources (RSS/WebSub/manual URL) when Composio is unavailable or unsuitable.
3. Automatic YouTube tutorial processing with transcript + optional visual analysis (Z.AI Vision), including degraded transcript-only mode.

This plan extends the current webhook foundation documented in `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/15_Webhook_Service_Implementation_2026-02-10.md`.

## Current State (Verified)

### Universal Agent webhook runtime

1. Inbound endpoint exists: `POST /api/v1/hooks/{subpath:path}`.
2. `HooksService` supports mapping + transforms + async dispatch.
3. Current auth path is bearer/token-based only.
4. Header matching is in schema but not enforced in matcher logic yet.

### Live Composio capability probe (project/account)

The following were validated against the live API on 2026-02-10:

1. Webhook subscriptions API is available:
   1. `GET /api/v3/webhook_subscriptions`
   2. `GET /api/v3/webhook_subscriptions/event_types`
   3. `POST /api/v3/webhook_subscriptions`
   4. `DELETE /api/v3/webhook_subscriptions/{id}`
2. Event types returned include:
   1. `composio.trigger.message`
   2. `composio.connected_account.expired`
3. Webhook subscription payload requires:
   1. `webhook_url`
   2. `enabled_events`
4. Subscription limit behavior is enforced:
   1. second active subscription create returns `WebhookSubscription_ResourceLimitExceeded` (409).
5. Trigger API is available via SDK:
   1. `triggers.list`, `triggers.get_type`, `triggers.create`, `triggers.delete`, `triggers.list_active`.
6. YouTube trigger types exist and are poll-based:
   1. `YOUTUBE_NEW_ACTIVITY_TRIGGER`
   2. `YOUTUBE_NEW_PLAYLIST_ITEM_TRIGGER`
   3. `YOUTUBE_NEW_PLAYLIST_TRIGGER`
   4. `YOUTUBE_NEW_SUBSCRIPTION_TRIGGER`
7. Current local SDK exposes trigger APIs but does not expose a first-class `webhook_subscriptions` client surface; webhook subscription management currently requires direct REST calls.

### Account Activation Snapshot (2026-02-10)

1. YouTube connected account is present and `ACTIVE` for the primary resolved user identity.
2. Existing active trigger instances: `0`.
3. Existing webhook subscriptions: `0` (after probe cleanup).
4. Trigger create/delete permission confirmed with temporary test trigger and cleanup.
5. Webhook subscription create/delete permission confirmed with temporary test subscription and cleanup.

## Architecture Decision

Adopt a dual-ingress model:

1. **Primary ingress**: Composio webhook subscription -> UA hooks endpoint.
2. **Fallback ingress**: Non-Composio providers (YouTube WebSub/RSS/custom webhook/manual URL).

Both ingress paths normalize into one internal event contract before dispatch.

## Target Design

### 1) Ingress Endpoints

1. Keep existing generic route:
   1. `POST /api/v1/hooks/{subpath:path}`
2. Reserve standardized subpaths:
   1. `/api/v1/hooks/composio`
   2. `/api/v1/hooks/youtube/rss`
   3. `/api/v1/hooks/youtube/websub`
   4. `/api/v1/hooks/youtube/manual`

### 2) Verification And Security

For `/hooks/composio`, verify signatures using raw body and headers:

1. `webhook-signature` (`v1,<base64_hmac>`)
2. `webhook-id`
3. `webhook-timestamp`

Signing formula:

1. `signing_string = "{webhook-id}.{webhook-timestamp}.{raw_body}"`
2. `expected = HMAC_SHA256(signing_string, COMPOSIO_WEBHOOK_SECRET)`

Security controls:

1. Timestamp tolerance window (default 5 minutes).
2. Replay protection keyed by `webhook-id` (TTL cache/store).
3. Constant-time signature compare.
4. Optional dual-auth mode (Composio HMAC OR bearer token) during migration.

### 3) Internal Event Contract

Normalize all sources into one event envelope:

```json
{
  "event_id": "string",
  "source": "composio|youtube_rss|youtube_websub|manual",
  "provider_event_type": "string",
  "occurred_at": "ISO-8601",
  "received_at": "ISO-8601",
  "dedupe_key": "string",
  "youtube": {
    "video_id": "string",
    "video_url": "string",
    "channel_id": "string",
    "channel_name": "string",
    "title": "string",
    "published_at": "ISO-8601"
  },
  "preferences": {
    "mode": "explainer_only|explainer_plus_code",
    "allow_degraded_transcript_only": true
  },
  "raw_payload": {}
}
```

### 4) Dispatch Contract

Map normalized event to agent message template and deterministic session key:

1. Session key example: `yt:{channel_id}:{video_id}`
2. Message template contains:
   1. source metadata
   2. target video URL
   3. processing mode
   4. required artifact output path

### 5) YouTube Processing Modes

Define explicit outcomes:

1. `full`
   1. transcript + visual analysis succeeded.
2. `degraded_transcript_only`
   1. transcript succeeded; visual unavailable/failed/too large.
3. `failed`
   1. transcript unavailable or unusable.

Rules:

1. Attempt transcript and video acquisition first.
2. Attempt Z.AI Vision whenever video is available.
3. For very long videos, allow sampled/segmented visual analysis.
4. Do not fail automatically on visual failure when transcript is sufficient and policy allows degraded mode.

## Required Changes To Existing Hooks Layer

### Priority A (must-have for Composio)

1. Add raw request body preservation for transform verification.
2. Implement `match.headers` evaluation (currently not enforced).
3. Add first-class auth strategy per mapping:
   1. `bearer`
   2. `composio_hmac`
   3. `none` (explicitly unsafe/test only)
4. Add replay guard utility.

### Priority B (operability)

1. Add structured hook processing logs:
   1. `source`, `event_type`, `mapping_id`, `action_kind`, `latency_ms`, `result`.
2. Add dead-letter behavior for transform failures.
3. Add idempotency store for processed `dedupe_key`s.

## Composio Subscription Strategy

Because project-level subscription limits apply, use one central Composio subscription:

1. Webhook URL -> `/api/v1/hooks/composio`
2. Enabled events:
   1. `composio.trigger.message`
   2. `composio.connected_account.expired`
3. Internally route by `payload.type` and trigger slug.

## Trigger Provisioning Strategy

Provision triggers per use case:

1. YouTube channel activity triggers for watched channels/playlists.
2. Optional non-YouTube triggers for adjacent workflows (Slack/GitHub/etc).

Fallback strategy when Composio trigger setup fails:

1. **Current scope decision (2026-02-10):** defer WebSub/RSS fallback implementation.
2. Keep manual URL ingestion available now.
3. Revisit WebSub and RSS only after Composio ingress is stable in production.

## Phased Implementation Plan

### Phase 0: Spec And Probe Baseline

1. Capture account capability probe script outputs.
2. Define secrets and env vars:
   1. `COMPOSIO_WEBHOOK_SECRET`
   2. `UA_HOOKS_TOKEN`
   3. `UA_HOOKS_ENABLED`

### Phase 1: Hook Runtime Hardening

1. Implement header matching.
2. Implement Composio HMAC verification transform/util.
3. Add replay protection.
4. Add tests for signature valid/invalid/replay/clock skew.

### Phase 2: Composio Adapter

1. Add transform to normalize Composio payload into internal envelope.
2. Add ops config mapping for `/hooks/composio`.
3. Add routing for `composio.trigger.message` into YouTube pipeline.

### Phase 3: YouTube Automation Runtime

1. Add YouTube event processor (subagent or dedicated service module).
2. Enforce output modes (`full`, `degraded_transcript_only`, `failed`) in manifest.
3. Wire to explainer-first skill invocation contract.

### Phase 4: Fallback Ingress (Deferred)

1. WebSub adapter route (deferred).
2. RSS poller fallback with dedupe (deferred).
3. Manual URL trigger endpoint is in active scope and should remain enabled.

### Phase 5: Observability And Ops

1. Add metrics dashboard:
   1. events received
   2. verification failures
   3. dedupe drops
   4. full/degraded/failed counts
2. Add runbook for rotating webhook secrets and replay cache cleanup.

## Test Plan

### Unit tests

1. Composio signature verification (positive/negative).
2. Header matching logic.
3. Normalization contract.
4. Deduping behavior.

### Integration tests

1. Mock Composio webhook -> mapped action enqueued.
2. Invalid signature -> 401.
3. Replay event -> dropped.
4. Long-video degraded-mode path.

### Live smoke tests

1. Create temp trigger.
2. Fire synthetic event.
3. Confirm artifact output + status mode.
4. Confirm cleanup (delete trigger).

## Risks And Mitigations

1. Composio API churn:
   1. Mitigation: version-pinned adapter, schema-tolerant parser, fallback ingress always available.
2. Single-subscription limit:
   1. Mitigation: central ingress with internal routing.
3. Signature spec changes:
   1. Mitigation: isolate verifier module + automated regression tests.
4. Long-video resource cost:
   1. Mitigation: segmented visual sampling + degraded transcript-only mode.

## Immediate Next Actions

1. Implement hook runtime hardening (Phase 1) before creating production subscription.
2. Add `/hooks/composio` mapping + verifier transform.
3. Draft the YouTube subagent contract and connect it to the normalized event envelope.
4. Provision initial YouTube trigger set for one channel as pilot.

## Scope Update (2026-02-10)

1. Prioritize Composio trigger ingress end-to-end.
2. Keep manual URL ingestion path active.
3. Defer YouTube fallback ingestion (WebSub/RSS) until after Composio rollout validation.

## Composio-First Quickstart

1. Ensure secrets are configured:
   1. `COMPOSIO_WEBHOOK_SECRET`
   2. `UA_HOOKS_TOKEN` (recommended for manual endpoint auth)
2. Bootstrap mappings (dry-run):
   1. `uv run python scripts/bootstrap_composio_youtube_hooks.py`
3. Write mappings to ops config:
   1. `uv run python scripts/bootstrap_composio_youtube_hooks.py --write`
4. Enable hooks and set token from env:
   1. `uv run python scripts/bootstrap_composio_youtube_hooks.py --write --enable-hooks --set-token-from-env`
5. Or set an explicit token directly:
   1. `uv run python scripts/bootstrap_composio_youtube_hooks.py --write --enable-hooks --token "<your-token>"`
6. Register/update Composio webhook subscription (recommended helper):
   1. `uv run python scripts/register_composio_webhook_subscription.py --webhook-url "https://<gateway-host>/api/v1/hooks/composio"`
   2. This helper handles single-subscription replacement and writes `COMPOSIO_WEBHOOK_SECRET` to `.env`.
7. Configure Composio subscription webhook URL (manual alternative):
   1. `https://<gateway-host>/api/v1/hooks/composio`
8. Manual URL ingestion endpoint remains:
   1. `POST /api/v1/hooks/youtube/manual`
