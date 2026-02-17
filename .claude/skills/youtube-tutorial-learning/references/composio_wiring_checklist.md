# Composio + Manual Ingress Wiring Checklist

Use this checklist to validate automated YouTube learning runs while keeping manual URL fallback available.

## 1) Prerequisites

1. Gateway route enabled: `POST /api/v1/hooks/{subpath:path}`.
2. Hook mappings bootstrapped (`composio` + `youtube/manual`).
3. Required secrets configured:
   1. `COMPOSIO_API_KEY`
   2. `COMPOSIO_WEBHOOK_SECRET`
   3. `UA_HOOKS_TOKEN`

## 2) Bootstrap Hook Mappings

From repo root:

```bash
uv run python scripts/bootstrap_composio_youtube_hooks.py --write --enable-hooks
```

Expected mappings:

1. `composio-youtube-trigger` -> `/api/v1/hooks/composio`
2. `youtube-manual-url` -> `/api/v1/hooks/youtube/manual`

## 3) Validate Manual Endpoint

```bash
curl -X POST "https://<gateway-host>/api/v1/hooks/youtube/manual" \
  -H "content-type: application/json" \
  -H "authorization: Bearer ${UA_HOOKS_TOKEN}" \
  -d '{
    "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "mode": "explainer_plus_code",
    "allow_degraded_transcript_only": true
  }'
```

Expected:

1. HTTP `202` accepted.
2. Session created in `AGENT_RUN_WORKSPACES/session_hook_*`.

## 4) Validate Composio Ingress

1. Confirm Composio trigger and webhook subscription are active.
2. Confirm `POST /api/v1/hooks/composio` receives events.
3. Confirm transformed action routes to `youtube-explainer-expert`.

## 5) Artifact Persistence Validation

After a trigger, verify both:

1. Session evidence under `AGENT_RUN_WORKSPACES/session_hook_*`.
2. Durable artifacts under resolved artifacts root (typically `/opt/universal_agent/artifacts/youtube-tutorial-learning/...`).

## 6) Path Hygiene Guardrail

Required policy for runs:

1. Do not use literal `UA_ARTIFACTS_DIR` path segments.
2. Invalid forms:
   1. `/opt/universal_agent/UA_ARTIFACTS_DIR/...`
   2. `UA_ARTIFACTS_DIR/...`
3. Use resolved absolute artifacts root instead.
