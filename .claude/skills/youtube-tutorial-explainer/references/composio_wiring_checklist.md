# Composio + Manual Ingress Wiring Checklist

Use this checklist to enable automated YouTube tutorial processing while keeping manual URL ingestion available.

## 1) Prerequisites

1. Public HTTPS gateway URL reachable by Composio.
2. Gateway endpoint route enabled: `POST /api/v1/hooks/{subpath:path}`.
3. Environment secrets set:
   1. `COMPOSIO_API_KEY`
   2. `COMPOSIO_WEBHOOK_SECRET` (after subscription create/rotate)
   3. `UA_HOOKS_TOKEN` (manual endpoint auth token)

## 2) Configure Hook Mappings

From repo root:

```bash
uv run python scripts/bootstrap_composio_youtube_hooks.py --write --enable-hooks
```

This ensures:

1. `composio-youtube-trigger` -> `/api/v1/hooks/composio`
2. `youtube-manual-url` -> `/api/v1/hooks/youtube/manual`
3. Manual auth token is read from `UA_HOOKS_TOKEN` env at runtime (not persisted in `ops_config.json`).

## 3) Register Composio Webhook Subscription

```bash
uv run python scripts/register_composio_webhook_subscription.py \
  --webhook-url "https://<your-public-host>/api/v1/hooks/composio"
```

Expected:

1. Project-level webhook subscription created/updated.
2. Secret rotated and written into `.env` (default behavior).

## 4) Validate Manual URL Ingestion (Always Keep Enabled)

```bash
curl -X POST "https://<your-public-host>/api/v1/hooks/youtube/manual" \
  -H "content-type: application/json" \
  -H "authorization: Bearer ${UA_HOOKS_TOKEN}" \
  -d '{
    "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "mode": "explainer_only",
    "allow_degraded_transcript_only": true
  }'
```

Expected:

1. 2xx response from hook ingress.
2. Agent run is dispatched with normalized YouTube message payload.

## 5) Validate Composio Ingress

1. In Composio dashboard, confirm trigger and webhook subscription are active.
2. Fire/observe a test event for configured YouTube trigger.
3. Confirm hook request reaches `/api/v1/hooks/composio`.
4. Confirm transform output dispatches an agent run.

## 6) Security Hardening

1. Prefer `secret_env` references over persisted plaintext token values in config.
2. Rotate `COMPOSIO_WEBHOOK_SECRET` periodically.
3. Rotate `UA_HOOKS_TOKEN` and avoid committing it.
4. Keep replay/timestamp validation enabled for HMAC strategy.
5. Keep manual endpoint token auth enabled even if Composio is primary.

## 7) Operational Fallback Policy

Current policy:

1. Primary: Composio ingress.
2. Mandatory backup: manual URL ingress.
3. Defer RSS/WebSub fallback implementation until Composio path is stable in production.
