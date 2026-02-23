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

Implementation status update (2026-02-22):
1. RSS watchlist now supports file-based loading via `watchlist_file`.
2. Active path uses `/opt/universal_agent/CSI_Ingester/development/channels_watchlist.json`.
3. VPS load confirmed with `channels=443` in `csi-ingester` logs.
4. Signed `youtube_channel_rss` events confirmed ingest (`200`) and durable storage in CSI.
5. UA internal manual-youtube dispatch is now intentionally restricted to `youtube_playlist` only.

### 7.3 RSS Acceptance Gates

1. `SOURCE_youtube_channel_rss_RECENT_TOTAL > 0` during test window.
2. `SOURCE_youtube_channel_rss_RECENT_DELIVERED == SOURCE_youtube_channel_rss_RECENT_TOTAL`.
3. `EVENTS_RECENT_UNDELIVERED=0`.
4. `DLQ_TOTAL=0` for RSS test period.
5. No regression in playlist event delivery.

## 8. RSS Notification And Reporting (Phase 5 Hardening)

Status (2026-02-22): Implemented in repo and ready for VPS enablement.

1. 10-minute Telegram digest:
   1. Script: `scripts/csi_rss_telegram_digest.py`
   2. systemd:
      1. `csi-rss-telegram-digest.service`
      2. `csi-rss-telegram-digest.timer`
   3. Behavior:
      1. batches new delivered RSS events by cursor,
      2. sends one message per 10-minute window only when events exist,
      3. supports optional Anthropic summarization via `CSI_RSS_DIGEST_USE_CLAUDE=1`.
2. Daily rollup summaries:
   1. Script: `scripts/csi_daily_summary.py`
   2. systemd:
      1. `csi-daily-summary.service`
      2. `csi-daily-summary.timer` (00:10 UTC, summarizes previous UTC day)
3. Installer:
   1. `/opt/universal_agent/CSI_Ingester/development/scripts/csi_install_systemd_extras.sh`
4. RSS semantic enrichment and trend analytics:
   1. `csi-rss-semantic-enrich.timer` (every 10 minutes, transcript + ai/non_ai + summary storage)
   2. `csi-rss-trend-report.timer` (hourly trend synthesis and UA event emit)

## 9. Token Telemetry To UA (Hourly)

Status (2026-02-22): Implemented in repo.

1. CSI records token usage in `token_usage` sqlite table (migration `0003_token_usage`).
2. Hourly reporter emits signed CSI event to UA:
   1. Script: `scripts/csi_hourly_token_report.py`
   2. Event source: `csi_analytics`
   3. Event type: `hourly_token_usage_report`
3. systemd:
   1. `csi-hourly-token-report.service`
   2. `csi-hourly-token-report.timer` (`OnCalendar=*:05`)
4. Report payload includes:
   1. hourly total prompt/completion/total tokens,
   2. per-process token totals,
   3. per-model token totals.

## 10. RSS Semantic Analytics And Trend Delivery

Status (2026-02-22): Implemented in repo and scheduled via systemd timers.

1. Event-level semantic enrichment:
   1. Script: `scripts/csi_rss_semantic_enrich.py`
   2. Table: `rss_event_analysis` (migration `0004_rss_analysis`)
   3. Output per RSS event:
      1. transcript status and transcript size,
      2. category (`ai` or `non_ai`),
      3. summary text,
      4. optional Claude usage tokens.
2. Hourly trend report:
   1. Script: `scripts/csi_rss_trend_report.py`
   2. Table: `trend_reports`
   3. Emits signed CSI event to UA:
      1. source: `csi_analytics`
      2. event_type: `rss_trend_report`
      3. includes top channels/themes and markdown report.
3. Token accounting integration:
   1. Claude calls in RSS digest, semantic enrich, and trend report each write to `token_usage`.
   2. Hourly token telemetry event keeps UA informed of process/model-level token consumption.

## 11. Tailnet Residential Transcript Worker (Implemented)

Purpose: bypass YouTube cloud-IP transcript blocking without introducing third-party residential proxies.

1. CSI RSS semantic enricher now supports transcript endpoint failover:
   1. `CSI_RSS_ANALYSIS_TRANSCRIPT_ENDPOINTS` (comma-separated, left-to-right retry),
   2. fallback to `CSI_RSS_ANALYSIS_TRANSCRIPT_ENDPOINT` if endpoint list is empty.
2. Recommended order:
   1. residential tailnet worker endpoint first,
   2. VPS-local endpoint (`127.0.0.1`) second.
3. Runtime behavior:
   1. per-event endpoint attempts are persisted in `rss_event_analysis.analysis_json`,
   2. `transcript_ref` stores source+endpoint host for diagnostics.
4. Operator action:
   1. set residential worker in `csi-ingester.env`,
   2. restart `csi-ingester`,
   3. force `csi-rss-semantic-enrich.service` and verify `RSS_ENRICH_TRANSCRIPT_OK` increases.
5. Reusable implementation primitive:
   1. `csi_ingester/net/egress_adapter.py`
   2. exposes generic failover + anti-bot detection for other blocked outbound calls.

## 12. UA Consumer Integration For CSI Analytics (Implemented)

Status (2026-02-23): Implemented in repo.

1. UA `/api/v1/signals/ingest` now dispatches two internal paths:
   1. playlist source (`youtube_playlist`) -> existing manual YouTube tutorial route,
   2. CSI analytics/analyst sources (`csi_analytics`, `csi_analyst`) -> internal UA trend/data-agent dispatch.
2. Mapping logic is centralized in `src/universal_agent/signals_ingest.py`:
   1. `to_manual_youtube_payload(...)`
   2. `to_csi_analytics_action(...)`
3. Hook routing now supports trusted direct action dispatch:
   1. `HooksService.dispatch_internal_action(...)`
   2. avoids dependence on external hooks mapping/auth for CSI-native analytics events.

## 13. Task Loop Activation + Quality Gates (Implemented)

Status (2026-02-23): Implemented in repo and wired for systemd deployment.

1. Recurring task bootstrap:
   1. Script: `scripts/csi_analysis_task_bootstrap.py`
   2. Unit/timer:
      1. `csi-analysis-task-bootstrap.service`
      2. `csi-analysis-task-bootstrap.timer` (hourly at `:03`)
   3. Function:
      1. auto-seeds baseline CSI analyst tasks when queue is idle,
      2. keeps `trend_followup`, `category_deep_dive`, `channel_deep_dive` active.
2. RSS quality gates:
   1. Script: `scripts/csi_rss_quality_gate.py`
   2. Unit/timer:
      1. `csi-rss-quality-gate.service`
      2. `csi-rss-quality-gate.timer` (every 15 minutes)
   3. Checks:
      1. recent RSS volume,
      2. undelivered and DLQ counts,
      3. transcript success ratio,
      4. staleness age,
      5. `other_interest` overflow ratio.
   4. Emits `rss_quality_gate_alert` / `rss_quality_gate_ok` CSI events to UA.

## 14. Report Product Finalization (Implemented)

Status (2026-02-23): Implemented in repo and wired for systemd deployment.

1. Script: `scripts/csi_report_product_finalize.py`
2. Unit/timer:
   1. `csi-report-product-finalize.service`
   2. `csi-report-product-finalize.timer` (hourly at `:35`)
3. Output:
   1. materialized product artifacts under `/opt/universal_agent/artifacts/csi-reports/<day>/product/`,
   2. emitted CSI event `report_product_ready` with artifact paths + token snapshot.

## 15. Reddit Discovery On-Deck

Status: next source onboarding after RSS hardening rollout verification.

1. Keep Reddit in discovery/scaffold phase first:
   1. adapter contract alignment,
   2. source-specific dedupe policy,
   3. low-risk canary subreddits.
2. Do not merge Reddit into production timer set until:
   1. RSS quality gate remains stable for full observation window,
   2. UA trend consumer confirms analytics signal usefulness from RSS pipeline.
