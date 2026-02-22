# Document 07. CSI Tailnet-First Deployment Plan v1 (2026-02-22)

## 1. Scope

This plan moves CSI ingestion from validated canary behavior to stable production operation on VPS using Tailnet-first access and controls.

Lane: VPS runtime (`root@100.106.113.93`)  
Date baseline: 2026-02-22

## 2. Current Baseline (Already Verified)

1. `tailscaled` is active on VPS and reachable over tailnet (`100.106.113.93`).
2. `csi-ingester` is active and polling playlist `PLjL3liQSixtsREpdYc959W_K7AIL_chHr` with `HTTP 200`.
3. Gateway ingest is accepting signed CSI events (`POST /api/v1/signals/ingest` -> `200`).
4. Hook dispatch is firing from CSI ingest.
5. CSI DB health is clean: delivered events present, undelivered `0`, DLQ `0`.

## 3. Tailnet-First Operating Profile

Use these defaults for all operator sessions:

```bash
export UA_VPS_HOST='root@100.106.113.93'
export UA_SSH_AUTH_MODE='tailscale_ssh'
export UA_TAILNET_PREFLIGHT='required'
```

Notes:
1. Primary control plane path is tailnet SSH, not public-IP SSH.
2. Keep key-based SSH only as break-glass fallback.

## 4. Deployment Phases

### Phase A - Freeze Known-Good CSI/Gateway

1. Ensure current CSI/gateway files are source-of-truth in repo.
2. Push only required files using `scripts/vpsctl.sh push`.
3. Restart only affected services:
   1. `scripts/vpsctl.sh restart gateway`
   2. `ssh root@100.106.113.93 'systemctl restart csi-ingester'`

Gate:
1. gateway `active`
2. csi-ingester `active`
3. no `401/403` CSI ingest auth failures in fresh logs

### Phase B - 24h Tailnet Canary Window

1. Keep legacy playlist timer disabled during CSI canary (or leave enabled only if explicit dual-run is desired).
2. Run hourly checks:
   1. CSI validator (`csi_parallel_validate.py`)
   2. gateway dispatch logs
   3. csi-ingester logs for YouTube poll and emit outcomes
3. Verify at least one real playlist addition is delivered end-to-end.

Gate:
1. `EVENTS_RECENT_UNDELIVERED=0`
2. `DLQ_TOTAL=0`
3. gateway shows `POST /api/v1/signals/ingest` with `200` and dispatch lines

### Phase C - Cutover Lock

1. Confirm legacy timer is stopped/disabled:
   1. `universal-agent-youtube-playlist-poller.timer`
   2. `universal-agent-youtube-playlist-poller.service`
2. Confirm CSI is the only active ingestion path for playlist additions.

Gate:
1. new playlist additions only appear as CSI event IDs (`yt:playlist:*`)

### Phase D - Post-Cutover Guardrails

1. Keep `UA_SIGNALS_INGEST_ALLOWED_INSTANCES` explicit (no wildcard).
2. Keep `CSI_INSTANCE_ID` fixed to `csi-vps-01`.
3. Keep `CSI_UA_SHARED_SECRET` and `UA_SIGNALS_INGEST_SHARED_SECRET` synchronized.
4. Retain replay capability via `csi_replay_dlq.py`.

Gate:
1. replay dry-run works
2. replay real-run works if test event injected

### Phase E - Rollback Contract

If CSI path regresses:

1. Stop CSI:
   1. `systemctl stop csi-ingester`
2. Re-enable legacy timer:
   1. `systemctl enable --now universal-agent-youtube-playlist-poller.timer`
3. Keep failed CSI entries in DLQ for replay after fix.

## 5. Operator Run Cadence

1. Hourly during canary: validator + logs.
2. Daily after cutover: validator + quick dispatch grep.
3. On every config change: signed smoke (`csi_emit_smoke_event.py --require-internal-dispatch`).

## 6. Acceptance Criteria (Production-Ready)

1. Tailnet-only ops path is stable for 24h.
2. At least two real playlist additions delivered successfully.
3. No auth failures (`401/403`) in fresh CSI->UA traffic.
4. No pending undelivered events and empty DLQ.
5. Rollback path tested and documented.

## 7. Next Workstream - RSS Investigation (Active Next Step)

After CSI playlist cutover stability is confirmed, the next planned phase is RSS investigation and rollout.

### 7.1 Scope

1. Enable and validate `youtube_channel_rss` as a first-class ingestion path.
2. Define behavior when the same video appears through both playlist and RSS paths.
3. Preserve current playlist reliability while expanding source coverage.

### 7.2 Execution Checklist

1. Configure RSS watchlist channels in `CSI_Ingester/development/config/config.yaml`.
2. Restart `csi-ingester` and verify service startup includes RSS adapter.
3. Run controlled RSS validation window:
   1. confirm new RSS-origin events are persisted,
   2. confirm signed delivery to UA returns `200`,
   3. confirm hook dispatch occurs.
4. Measure duplicate behavior across sources:
   1. keep both events (source-specific),
   2. or suppress second source by policy.
5. Finalize dedupe policy and document it in PRD/runbook.

### 7.3 RSS Acceptance Gates

1. `SOURCE_youtube_channel_rss_RECENT_TOTAL > 0` during test window.
2. `SOURCE_youtube_channel_rss_RECENT_DELIVERED == SOURCE_youtube_channel_rss_RECENT_TOTAL`.
3. `EVENTS_RECENT_UNDELIVERED=0`.
4. `DLQ_TOTAL=0` for RSS test period.
5. No regression in playlist event delivery.
