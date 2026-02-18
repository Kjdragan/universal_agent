# Hybrid YouTube Ingestion Runbook (VPS Control Plane + Local Worker)

Date: 2026-02-18  
Status: Implemented

## Goal

Keep orchestration on VPS, but move fragile YouTube transcript extraction to a local/Tailscale-reachable worker IP to reduce anti-bot blocking.

## Architecture

1. Composio webhook lands on VPS: `/api/v1/hooks/composio`.
2. `HooksService` (VPS) enters `local_worker` ingest mode for YouTube routes.
3. VPS calls local worker endpoint over Tailscale/reverse tunnel: `/api/v1/youtube/ingest`.
4. Local worker extracts transcript with:
   - `yt-dlp` caption path first
   - `youtube-transcript-api` fallback
5. VPS stores returned transcript in session workspace:
   - `<session_workspace>/ingestion/youtube_transcript.local.txt`
   - `<session_workspace>/ingestion/youtube_local_ingest_result.json`
6. VPS then dispatches normal YouTube subagent run with transcript file hints injected.

If local ingestion fails and fail-closed is enabled:
- VPS does **not** dispatch degraded run.
- VPS writes `<session_workspace>/pending_local_ingest.json`.

## Config

### VPS (control plane)

- `UA_HOOKS_YOUTUBE_INGEST_MODE=local_worker`
- `UA_HOOKS_YOUTUBE_INGEST_URL=http://127.0.0.1:18002/api/v1/youtube/ingest`
  - or direct Tailscale URL to local worker.
- `UA_HOOKS_YOUTUBE_INGEST_TOKEN=<shared-token>` (recommended)
- `UA_HOOKS_YOUTUBE_INGEST_TIMEOUT_SECONDS=120`
- `UA_HOOKS_YOUTUBE_INGEST_RETRY_ATTEMPTS=3`
- `UA_HOOKS_YOUTUBE_INGEST_RETRY_DELAY_SECONDS=20`
- `UA_HOOKS_YOUTUBE_INGEST_FAIL_OPEN=0` (recommended for quality)

### Local worker (gateway process)

- Run local gateway on `:8002`.
- Configure auth token (recommended):
  - `UA_YOUTUBE_INGEST_TOKEN=<shared-token>`
- Keep local gateway alive as a user service:
  - `scripts/install_local_gateway_user_service.sh`
- Keep tunnel up (if using reverse SSH path):
  - `scripts/install_ua_youtube_forward_tunnel_user_service.sh`

## Endpoint Contract

### Request
`POST /api/v1/youtube/ingest`

```json
{
  "video_url": "https://www.youtube.com/watch?v=dxlyCPGCvy8",
  "video_id": "dxlyCPGCvy8",
  "language": "en",
  "timeout_seconds": 120,
  "max_chars": 180000,
  "request_id": "session_hook_yt_..."
}
```

### Response (success)

```json
{
  "ok": true,
  "status": "succeeded",
  "video_url": "...",
  "video_id": "...",
  "transcript_text": "...",
  "transcript_chars": 12345,
  "transcript_truncated": false,
  "source": "yt_dlp_srt",
  "attempts": []
}
```

## Operational Checks

1. Verify local worker endpoint auth and health.
2. Trigger webhook resend.
3. Confirm VPS log lines:
   - ingest mode `local_worker`
   - transcript file written under `ingestion/`
4. Confirm subagent prompt includes:
   - `local_youtube_ingest_status: succeeded`
   - transcript file path.
5. If failure, confirm pending marker exists and no degraded dispatch when fail-closed.

## Notes

- Tailscale enables remote access to local services, but local machine availability still governs uptime.
- For higher reliability, host local worker on an always-on home node.
