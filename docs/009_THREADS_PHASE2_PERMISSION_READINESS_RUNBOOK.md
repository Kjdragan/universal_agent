# 009 - Threads Phase 2 Permission Readiness Runbook

## Purpose

Operational runbook to move Threads write-path from gated canary to usable Phase 2,
with clear preflight checks and explicit failure signatures.

## Current Status

Phase 2 canary is configured and write-path preflight now passes via fallback
verification when Meta debug-token is unstable.

Current observed preflight signature:

- `THREADS_PUBLISH_PREFLIGHT` includes `"ok": true`
- `reason: "write_probe_verified"`
- `scope_verification: "write_probe_fallback"`
- write probe detail often includes `code: 100` (`text required`) which confirms
  endpoint-level write access without creating a post.

Write operation remains gated by governance until approval/caps are satisfied:

- `threads_publish_approval_required` (expected in `manual_confirm` mode)

Quota behavior (updated):

1. Dry-run attempts do not consume daily post/reply caps.
2. Failed live API attempts do not consume daily post/reply caps.
3. Caps are committed only after successful live write operations.

## What is already automated

1. Write governance gates:
   - `CSI_THREADS_PUBLISHING_ENABLED`
   - `CSI_THREADS_PUBLISH_DRY_RUN`
   - `CSI_THREADS_PUBLISH_APPROVAL_MODE`
   - daily caps + persisted state + JSONL audit
2. Phase-2 smoke tool:
   - `scripts/csi_threads_publish_smoke.py`
3. Default preflight gate in smoke:
   - env/token sanity
   - `/me` identity check
   - scope/debug check (`debug_token`)
   - fallback non-destructive write probe

## Manual actions required (Meta side)

1. Ensure app has write permissions approved for Threads write actions.
2. Re-run OAuth consent with write scopes included.
3. Exchange new auth code and refresh runtime secrets.

## Required scopes for live write canary

Minimum:

1. `threads_basic`
2. `threads_content_publish`

If reply automation is intended in Phase 2, include the reply-management scope
required by the current Threads API policy.

## Step-by-step

### 1) Generate write-scope auth URL

```bash
cd /opt/universal_agent/CSI_Ingester/development
scripts/csi_run.sh uv run python3 scripts/csi_threads_auth_bootstrap.py \
  --print-auth-url \
  --app-id "<THREADS_APP_ID>" \
  --redirect-uri "https://app.clearspringcg.com/threads-callback" \
  --scopes "threads_basic,threads_content_publish,threads_read_replies,threads_manage_mentions,threads_manage_insights,threads_keyword_search,threads_profile_discovery"
```

### 2) Exchange returned code

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_auth_bootstrap.py \
  --mode exchange \
  --app-id "<THREADS_APP_ID>" \
  --app-secret "<THREADS_APP_SECRET>" \
  --auth-code "<NEW_OAUTH_CODE>" \
  --redirect-uri "https://app.clearspringcg.com/threads-callback" \
  --skip-env-write \
  --infisical-json-file /tmp/threads-secrets.json
```

### 3) Sync to Infisical

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_infisical_sync.py \
  --updates-file /tmp/threads-secrets.json
```

### 4) Verify preflight passes

```bash
set -a
source /opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env
set +a

scripts/csi_run.sh uv run --active python3 scripts/csi_threads_publish_smoke.py \
  --config-path config/config.yaml \
  --operation create \
  --media-type TEXT \
  --text "phase2 preflight check" \
  --approval-id "threads-phase2-preflight-001"
```

Expected:

1. `THREADS_PUBLISH_PREFLIGHT` with `"ok": true`
2. No `missing_write_permission` error

### 5) Controlled canary (still manual approval mode)

Use low caps and keep manual mode:

```bash
CSI_THREADS_PUBLISH_DRY_RUN=0
CSI_THREADS_PUBLISH_APPROVAL_MODE=manual_confirm
CSI_THREADS_PUBLISH_MAX_DAILY_POSTS=2
CSI_THREADS_PUBLISH_MAX_DAILY_REPLIES=3
```

Run one create-only canary:

```bash
scripts/csi_run.sh uv run --active python3 scripts/csi_threads_publish_smoke.py \
  --config-path config/config.yaml \
  --operation create \
  --media-type TEXT \
  --text "phase2 controlled canary" \
  --approval-id "threads-phase2-canary-001" \
  --audit-actor "threads-rollout-bot" \
  --audit-reason "phase2 controlled canary"
```

Audit trail:

- `/var/lib/universal-agent/csi/threads_publishing_audit.jsonl`
- Includes `approval_ref`, `actor`, `reason`, `payload_hash`, `response_id`.

## Troubleshooting map

1. `missing_write_permission`:
   - Meta app lacks write permission readiness or token missing write scope.
2. `threads_publishing_disabled_phase1`:
   - `CSI_THREADS_PUBLISHING_ENABLED` not set to `1`.
3. `threads_publish_daily_post_limit_reached`:
   - reset/roll day in `CSI_THREADS_PUBLISH_STATE_PATH` for controlled canary.
4. `threads_publish_approval_required`:
   - manual mode requires `--approval-id`.
