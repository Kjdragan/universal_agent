# 008 - Threads Rollout Next Phases (Post-Phase-1)

## Purpose

Define what remains after Phase 1 Threads analytics rollout and how to execute it in a controlled sequence.

Phase 1 status baseline (already completed):

1. Threads source adapters live (`threads_owned`, `threads_trends_seeded`, `threads_trends_broad`)
2. Token bootstrap + refresh + Infisical sync automation
3. Threads semantic enrichment and Threads narrative report generation
4. Global brief integration includes Threads source totals
5. Webhook + publishing scaffolds exist but are gated/disabled

## Phase 1.5 (Immediate Hardening and Verification)

Goal: prove stability and visibility before write-side activation.

### Deliverables

1. Strict all-source probe mode:
   - `csi_threads_probe.py --require-all` fails unless owned + seeded + broad all pass.
2. Post-rollout verifier:
   - `csi_threads_rollout_verify.py` checks:
   - live probe result
   - event freshness in DB (lookback window, based on `received_at`)
   - semantic analysis rows
   - Threads trend report presence
   - latest global brief Threads contribution
   - seeded adapter cycle diagnostics (`last_cycle` from `source_state`)
   - webhook freshness gate (when webhook is enabled):
     - `--require-webhook-activity` enforces recent webhook ingest in lookback.
   - seeded warning noise suppression for constrained polls:
     - rate-limited or timeout-aborted seeded cycles now emit
       `seeded_poll_constrained_recently` instead of a false-positive
       “live but no new events” warning.
3. Daily verification timer:
   - `csi-threads-rollout-verify.timer` at `03:35 UTC` (after token refresh window).
4. Seeded-resilience controls:
   - auto-degrade seeded term query limits when Threads returns `code:1` reduce-data errors
   - halt seeded cycle early on rate-limit/timeouts to preserve quota and poll latency

### Manual run commands

```bash
cd /opt/universal_agent/CSI_Ingester/development

scripts/csi_run.sh uv run python3 scripts/csi_threads_probe.py \
  --config-path config/config.yaml \
  --source all \
  --limit 5 \
  --max-terms 3 \
  --require-all

scripts/csi_run.sh uv run python3 scripts/csi_threads_rollout_verify.py \
  --config-path config/config.yaml \
  --db-path /var/lib/universal-agent/csi/csi.db \
  --lookback-hours 24 \
  --write-json /opt/universal_agent/artifacts/csi/threads_rollout_verify/latest.json
```

Note:

1. Verifier auto-hydrates `THREADS_*` probe credentials from
   `deployment/systemd/csi-ingester.env` when they are missing from shell env.

Optional strict seeded enforcement for canary windows:

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_rollout_verify.py \
  --config-path config/config.yaml \
  --db-path /var/lib/universal-agent/csi/csi.db \
  --lookback-hours 24 \
  --require-seeded-events
```

### Success criteria

1. Strict probe exits `0` in two consecutive runs.
2. Verifier exits `0` and reports:
   - owned events > 0 in lookback
   - analysis rows > 0
3. Threads trend report generated in lookback.

## Phase 2 (Write Path Activation - Gated)

Goal: safely enable posting/reply actions.

Current implementation status:

1. Core methods implemented in `threads_publishing.py` with real API calls:
   - create container
   - publish container
   - reply to post
2. Governance gates implemented:
   - global enable flag
   - dry-run default
   - manual approval requirement
   - daily post/reply caps with persisted state
   - JSONL audit trail (`CSI_THREADS_PUBLISH_AUDIT_PATH`)
3. Smoke script added:
   - `scripts/csi_threads_publish_smoke.py`
4. Preflight gate added (default-on in smoke script):
   - token/env readiness check
   - scope verification (`debug_token`)
   - `/me` identity match
   - fallback non-destructive write probe for permission denial (`code:10`)

### Scope

1. Add execution policy:
   - `dry_run` default true
   - max posts/replies per day
   - explicit approval mode (`manual_confirm` vs autonomous)
2. Expand audit trail detail:
   - actor, reason, payload hash, resulting media/reply IDs
   - optional approval record linkage in audit JSONL entries
3. Add canary audit verifier:
   - `scripts/csi_threads_publish_canary_verify.py`
   - validates recent JSONL audit activity and error-rate thresholds
   - optional strict gate to require at least one successful live write in lookback

### Activation prerequisites

1. App permissions approved for required write scopes.
2. Runbook + kill switch documented.
3. Canary mode completed with manual approvals.

### Recommended canary sequence (manual approval mode)

1. Preflight-only validation via smoke script default behavior:
   - Fails fast if write scopes are missing.
2. `dry_run=1` for 24-48h with audit JSONL review.
3. Controlled `dry_run=0` with low caps.
4. Keep `CSI_THREADS_PUBLISH_APPROVAL_MODE=manual_confirm` until stable.
5. Run canary verifier in lookback windows to track stability:

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_publish_canary_verify.py \
  --audit-path /var/lib/universal-agent/csi/threads_publishing_audit.jsonl \
  --lookback-hours 48 \
  --min-records 1 \
  --max-error-rate 0.60 \
  --write-json /opt/universal_agent/artifacts/csi/threads_publish_canary_verify/latest.json
```

Observed blocker signature for missing write permission:

- `threads_publish_create_failed:http_500: ... "Application does not have permission for this action" ... code:10`

## Phase 3 (Webhook-First Hybrid)

Goal: shift from poll-only to poll + webhook for lower latency and better freshness.

Current status:

1. `GET /webhooks/threads` verification gate live (env-gated).
2. `POST /webhooks/threads` now performs signed intake + normalization + dedupe + storage + UA emit path.
3. Webhook ingest state telemetry persisted at `source_state` key `threads_webhook:state`.
4. Public app route now forwards Meta webhook calls to CSI ingester:
   - `https://app.clearspringcg.com/webhooks/threads` -> `http://127.0.0.1:8091/webhooks/threads`
5. Webhook canary automation added:
   - `csi-threads-webhook-canary-verify.service`
   - `csi-threads-webhook-canary-verify.timer` (every 2 hours)
   - uses stable media id for dedupe-safe repeated signed ingest verification.

### Scope

1. Enable webhook endpoints in production:
   - verification token
   - signed payload validation
2. Reconcile webhook + polling duplicates via dedupe keys (`threads:{media_id}` where available).
3. Tune webhook event-type mapping once real payload mix is observed in production.

### Success criteria

1. Webhook events accepted and visible in CSI event stream.
2. Duplicate suppression holds under mixed poll/webhook traffic.
3. No regression in trend report quality and cadence.

### Webhook smoke canary

```bash
cd /opt/universal_agent/CSI_Ingester/development
scripts/csi_run.sh uv run python3 scripts/csi_threads_webhook_smoke.py \
  --base-url "https://app.clearspringcg.com" \
  --verify \
  --ingest \
  --fixed-media-id \
  --write-json /opt/universal_agent/artifacts/csi/threads_webhook_canary_verify/latest.json
```

## Operational Notes

1. Poll cadence does not need immediate change:
   - Owned 15m, Seeded 15m, Broad 30m.
2. Current quality bottleneck is not collection cadence; it is verification depth and controlled write activation.
3. Keep Phase 2 disabled until Phase 1.5 passes consistently.
