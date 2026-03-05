# CSI Ingester Development

Standalone implementation workspace for CSI Ingester v1.

## Quick start

```bash
cd CSI_Ingester/development
source scripts/csi_dev_env.sh
scripts/csi_run.sh uv run uvicorn csi_ingester.app:app --host 0.0.0.0 --port 8091
```

Run tests with the same wrapper:

```bash
scripts/csi_run.sh uv run --group dev pytest tests/unit/test_signature.py -q
```

Run preflight checks:

```bash
scripts/csi_run.sh scripts/csi_preflight.sh
scripts/csi_run.sh scripts/csi_preflight.sh --strict
```

Run parallel-run snapshot checks:

```bash
scripts/csi_run.sh python3 scripts/csi_parallel_validate.py --db-path /path/to/csi.db --since-minutes 60
```

Run signed CSI->UA smoke check:

```bash
uv run python scripts/csi_local_e2e_smoke.py
```

Run data-plane validation (RSS/Reddit ingest + optional live smoke to UA):

```bash
scripts/csi_run.sh python3 scripts/csi_validate_live_flow.py --lookback-hours 24 --emit-smoke
```

Run endpoint smoke against a live UA endpoint:

```bash
PYTHONPATH=src:CSI_Ingester/development .venv/bin/python CSI_Ingester/development/scripts/csi_emit_smoke_event.py --require-internal-dispatch
```

Run RSS digest in dry-run mode (no Telegram send):

```bash
scripts/csi_run.sh python3 scripts/csi_rss_telegram_digest.py --db-path /path/to/csi.db --seed-current-on-first-run --dry-run
```

Run Reddit digest in dry-run mode (no Telegram send):

```bash
scripts/csi_run.sh python3 scripts/csi_reddit_telegram_digest.py --db-path /path/to/csi.db --seed-current-on-first-run --dry-run
```

Run playlist tutorial digest in dry-run mode (no Telegram send):

```bash
scripts/csi_run.sh python3 scripts/csi_playlist_tutorial_digest.py --db-path /path/to/csi.db --seed-current-on-first-run --dry-run
```

If Telegram chat routing is not configured yet (`CSI_RSS_TELEGRAM_CHAT_ID` unset), the digest job now exits cleanly in "skipped" mode instead of failing the systemd service.
Digest format includes adaptive category sections. Core categories are `AI`, `Political`, `War`, `Other Interest`; additional dynamic categories can be created automatically from recurring topics (capped by max category settings).

Telegram channel separation options:

- `CSI_RSS_TELEGRAM_CHAT_ID` for YouTube RSS digest stream.
- `CSI_REDDIT_TELEGRAM_CHAT_ID` for Reddit digest stream.
- `CSI_TUTORIAL_TELEGRAM_CHAT_ID` for playlist tutorial updates (new playlist videos + discovered artifact paths).
- `CSI_TUTORIAL_ARTIFACTS_BASE_URL` (optional) to include clickable artifact URLs in tutorial digest messages (for example `https://api.clearspringcg.com`).
- Optional per-stream Telegram forum topic IDs:
- `CSI_RSS_TELEGRAM_THREAD_ID`
- `CSI_REDDIT_TELEGRAM_THREAD_ID`
- `CSI_TUTORIAL_TELEGRAM_THREAD_ID`
- Strict stream routing controls:
- `CSI_TELEGRAM_STRICT_STREAM_ROUTING=1` (global)
- `CSI_REDDIT_TELEGRAM_STRICT_STREAM_ROUTING=1`
- `CSI_TUTORIAL_TELEGRAM_STRICT_STREAM_ROUTING=1`
- `csi-reddit-telegram-digest.service` and `csi-playlist-tutorial-digest.service` run with `--strict-stream-routing` by default, so those streams do not silently fall back into RSS/default chat routing.
- Playlist tutorial digest now has built-in follow-up behavior:
- when a new playlist video is first detected but no tutorial artifact exists yet, it is tracked in pending state;
- on later timer runs, once artifacts appear, CSI sends a second "Tutorial Artifacts Ready" message automatically and clears that pending item.
- if artifacts remain pending past threshold, CSI now sends periodic pending reminder messages with workspace hints instead of staying silent.
- pending reminder controls:
- `CSI_TUTORIAL_PENDING_REMINDER_MINUTES` (default `30`)
- `CSI_TUTORIAL_PENDING_REMINDER_COOLDOWN_MINUTES` (default `120`)
- `CSI_TUTORIAL_PENDING_MAX_AGE_HOURS` (default `12`; stale pending items older than this are dropped to avoid resurfacing old videos forever)
- historical pending backfill is now opt-in (`--backfill-pending-count`, default `0`) to prevent old playlist rows from reappearing when no new videos were added.
- playlist tutorial digest also runs a stalled-turn watchdog:
- it scans matching tutorial workspaces for `turn_started` entries without `turn_finalized`;
- if a turn stays open beyond threshold, CSI sends a "Tutorial Session Stalled" alert into the tutorial Telegram stream with workspace hints.
- stalled-turn controls:
- `CSI_TUTORIAL_STALLED_TURN_MINUTES` (default `15`)
- `CSI_TUTORIAL_STALLED_TURN_COOLDOWN_MINUTES` (default `90`)
- `CSI_TUTORIAL_WORKSPACE_ROOT` (optional; defaults to `/opt/universal_agent/AGENT_RUN_WORKSPACES`)

Install periodic systemd jobs on VPS (requires root):

```bash
/opt/universal_agent/CSI_Ingester/development/scripts/csi_install_systemd_extras.sh
```

Timers installed:

- `csi-rss-telegram-digest.timer` -> every 10 minutes (sends one batched Telegram digest when new RSS events exist)
- `csi-reddit-telegram-digest.timer` -> every 10 minutes at `:01` (sends one batched Telegram digest when new Reddit watchlist events exist)
- `csi-playlist-tutorial-digest.timer` -> every 10 minutes at `:04` (playlist-triggered tutorial updates with artifact paths)
- `csi-rss-semantic-enrich.timer` -> every 10 minutes at `:02` (transcript extraction + adaptive semantic categorization)
- `csi-rss-trend-report.timer` -> hourly at minute `:12` (aggregated trend report event to UA)
- `csi-reddit-trend-report.timer` -> hourly at minute `:18` (aggregated Reddit trend report event to UA)
- `csi-rss-insight-analyst.timer` -> hourly at minute `:22` (CSI-native insight reports: emerging + daily cadence)
- `csi-rss-reclassify-categories.timer` -> every 6 hours at minute `:17` (reclassify older RSS rows with current taxonomy)
- `csi-category-quality-loop.timer` -> hourly at minute `:27` (adaptive taxonomy quality loop + threshold/category tuning)
- `csi-rss-quality-gate.timer` -> every 15 minutes (SLO-style quality gates + alert event emission)
- `csi-replay-dlq.timer` -> every 15 minutes at `:08` (replays failed CSI->UA deliveries from dead-letter queue)
- `csi-analysis-task-runner.timer` -> every 10 minutes at `:06` (runs UA-submitted CSI analysis tasks)
- `csi-analysis-task-bootstrap.timer` -> hourly at minute `:03` (auto-seeds baseline recurring analysis tasks)
- `csi-report-product-finalize.timer` -> hourly at minute `:35` (materializes report artifacts + emits `report_product_ready`)
- `csi-daily-summary.timer` -> daily at `00:10 UTC` (writes summary artifacts under `/opt/universal_agent/artifacts/csi-reports/<day>/`)
- `csi-hourly-token-report.timer` -> hourly at minute 05 (sends `hourly_token_usage_report` event to UA)
- `csi-threads-token-refresh-sync.timer` -> daily at `03:15 UTC` with jitter (refreshes Threads token, syncs to Infisical, runs owned probe)
- `csi-threads-rollout-verify.timer` -> daily at `03:35 UTC` (strict all-source Threads probe + DB/report evidence verification)

Run hourly token report manually:

```bash
scripts/csi_run.sh python3 scripts/csi_hourly_token_report.py --db-path /path/to/csi.db --force
```

Replay DLQ entries manually:

```bash
scripts/csi_run.sh python3 scripts/csi_replay_dlq.py --db-path /path/to/csi.db --limit 100 --max-attempts 3
```

Run RSS semantic enrichment manually:

```bash
scripts/csi_run.sh python3 scripts/csi_rss_semantic_enrich.py --db-path /path/to/csi.db --max-events 12
```

Adaptive category behavior:

- Base categories are always present: `ai`, `political`, `war`, `other_interest`.
- CSI can auto-create new categories when recurring `other_interest` topics emerge.
- Category count is capped (`--max-categories` or `CSI_RSS_ANALYSIS_MAX_CATEGORIES`, default `10`).
- When capped, CSI retires the narrowest dynamic category first to keep taxonomy broad and avoid uncategorized spillover.
- Low-signal/trivial dynamic labels (e.g. pronoun/short-form noise) are blocked from category creation.
- Core-topic tokens are forced back into core categories (`ai`, `political`, `war`) instead of becoming one-off dynamic categories.

Run RSS trend report manually:

```bash
scripts/csi_run.sh python3 scripts/csi_rss_trend_report.py --db-path /path/to/csi.db --window-hours 24 --force
```

Run Reddit trend report manually:

```bash
scripts/csi_run.sh python3 scripts/csi_reddit_trend_report.py --db-path /path/to/csi.db --window-hours 24 --force
```

Run CSI insight analyst manually:

```bash
scripts/csi_run.sh python3 scripts/csi_rss_insight_analyst.py --db-path /path/to/csi.db --force
```

Run adaptive category reclassification manually:

```bash
scripts/csi_run.sh /opt/universal_agent/CSI_Ingester/development/.venv/bin/python scripts/csi_rss_reclassify_categories.py --db-path /path/to/csi.db --max-rows 1500
```

Run category quality loop manually:

```bash
scripts/csi_run.sh python3 scripts/csi_category_quality_loop.py --db-path /path/to/csi.db --force
```

Run analysis task runner manually:

```bash
scripts/csi_run.sh python3 scripts/csi_analysis_task_runner.py --db-path /path/to/csi.db --max-tasks 8
```

Run analysis task bootstrap manually:

```bash
scripts/csi_run.sh python3 scripts/csi_analysis_task_bootstrap.py --db-path /path/to/csi.db --force
```

Run RSS quality gate manually:

```bash
scripts/csi_run.sh python3 scripts/csi_rss_quality_gate.py --db-path /path/to/csi.db --window-hours 6 --force
```

Run report product finalization manually:

```bash
scripts/csi_run.sh python3 scripts/csi_report_product_finalize.py --db-path /path/to/csi.db --window-hours 24 --force
```

## Reddit Canary Activation

Reddit ingestion is scaffolded and disabled by default. Use canary mode to validate source quality safely.

1. Probe the watchlist without ingesting events:

```bash
scripts/csi_run.sh python3 scripts/csi_reddit_probe.py --watchlist-file /opt/universal_agent/CSI_Ingester/development/reddit_watchlist.json
```

2. Enable/disable canary ingestion:

```bash
/opt/universal_agent/CSI_Ingester/development/scripts/csi_reddit_canary_setup.sh enable
/opt/universal_agent/CSI_Ingester/development/scripts/csi_reddit_canary_setup.sh disable
```

Default canary watchlist file:

- `/opt/universal_agent/CSI_Ingester/development/reddit_watchlist.json`

## Threads Channel Setup (Phase 1: Analytics Only)

Threads ingestion is implemented behind three adapters and defaults to disabled:

- `threads_owned` (owned account posts/mentions/replies/insights)
- `threads_trends_seeded` (seeded keyword/tag packs)
- `threads_trends_broad` (broad crawl + adaptive expansion)

For a full step-by-step "no secret-by-secret entry" setup, use:

- `THREADS_INFISICAL_SETUP.md`

### 1) Configure credentials

Set these in CSI runtime env (for example `deployment/systemd/csi-ingester.env`):

- `THREADS_APP_ID`
- `THREADS_APP_SECRET`
- `THREADS_USER_ID`
- `THREADS_ACCESS_TOKEN`
- `THREADS_TOKEN_EXPIRES_AT`

Bootstrap helper script (recommended):

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_auth_bootstrap.py --print-auth-url --redirect-uri "https://your-redirect.example/callback"
```

Then, after you complete Meta consent, exchange and persist directly from the returned OAuth `code`:

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_auth_bootstrap.py \
  --mode exchange \
  --auth-code "<THREADS_OAUTH_CODE>" \
  --redirect-uri "https://your-redirect.example/callback"
```

If you already have a short-lived token, you can still pass `--short-lived-token`.

Refresh later (updates `THREADS_ACCESS_TOKEN` and `THREADS_TOKEN_EXPIRES_AT` in env file):

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_auth_bootstrap.py --mode refresh
```

If `THREADS_USER_ID` is empty, the bootstrap script attempts to resolve it from
`/v1.0/me` automatically (disable with `--no-resolve-user-id`).

Infisical-first flow (no local env-file write):

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_auth_bootstrap.py \
  --mode exchange \
  --short-lived-token "<SHORT_LIVED_TOKEN>" \
  --skip-env-write \
  --print-infisical-json
```

Infisical bulk sync flow (recommended, no secret-by-secret entry):

```bash
# 1) Exchange token and write a JSON payload
scripts/csi_run.sh uv run python3 scripts/csi_threads_auth_bootstrap.py \
  --mode exchange \
  --auth-code "<THREADS_OAUTH_CODE>" \
  --redirect-uri "https://your-redirect.example/callback" \
  --skip-env-write \
  --infisical-json-file /tmp/threads-secrets.json

# 2) Upsert all keys in one call
scripts/csi_run.sh uv run python3 scripts/csi_threads_infisical_sync.py \
  --updates-file /tmp/threads-secrets.json
```

Infisical machine identity settings required for the sync command:

- `INFISICAL_CLIENT_ID`
- `INFISICAL_CLIENT_SECRET`
- `INFISICAL_PROJECT_ID`
- `INFISICAL_ENVIRONMENT` (default: `dev`)
- `INFISICAL_SECRET_PATH` (default: `/`)

### 1b) Automated daily refresh + Infisical sync (systemd timer)

Refresh/sync runner script:

- `scripts/csi_threads_token_refresh_sync.sh`

Systemd units:

- `deployment/systemd/csi-threads-token-refresh-sync.service`
- `deployment/systemd/csi-threads-token-refresh-sync.timer`

Install/enable via the existing extras installer (includes this timer now):

```bash
sudo /opt/universal_agent/CSI_Ingester/development/scripts/csi_install_systemd_extras.sh
```

Check status:

```bash
systemctl status csi-threads-token-refresh-sync.timer
journalctl -u csi-threads-token-refresh-sync.service -n 100 --no-pager
```

Required env keys in `deployment/systemd/csi-ingester.env` for this automation:

- `INFISICAL_CLIENT_ID`
- `INFISICAL_CLIENT_SECRET`
- `INFISICAL_PROJECT_ID`
- `INFISICAL_ENVIRONMENT` (`dev` by default)
- `INFISICAL_SECRET_PATH` (`/` by default)

Optional control knobs:

- `CSI_THREADS_REFRESH_TIMEOUT_SECONDS` (default `20`)
- `CSI_THREADS_REFRESH_BUFFER_SECONDS` (default `21600`)
- `CSI_THREADS_REFRESH_RUN_PROBE` (`1` default)
- `CSI_THREADS_REFRESH_REQUIRE_PROBE_OK` (`1` default)
- `CSI_THREADS_PROBE_SOURCE` (`owned` default)
- `CSI_THREADS_PROBE_LIMIT` (`3` default)

Post-refresh verification timer (recommended for rollout hardening):

- `deployment/systemd/csi-threads-rollout-verify.service`
- `deployment/systemd/csi-threads-rollout-verify.timer`

Check status:

```bash
systemctl status csi-threads-rollout-verify.timer
journalctl -u csi-threads-rollout-verify.service -n 120 --no-pager
```

### 2) Enable adapters in `config/config.yaml`

Toggle `enabled: true` for one or more of:

- `sources.threads_owned`
- `sources.threads_trends_seeded`
- `sources.threads_trends_broad`

Set poll intervals to `900` seconds for the default 15-minute cadence.

### 3) Configure seeded/broad trend inputs

- Seeded domain terms:
  - `sources.threads_trends_seeded.query_packs[].terms`
  - `sources.threads_trends_seeded.seed_terms`
- Broad crawl baseline:
  - `sources.threads_trends_broad.query_pool`

Run live Threads probe before enabling adapters in production:

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_probe.py --config-path config/config.yaml --source all --limit 5 --max-terms 3
```

Run strict all-source Threads probe (fails unless owned + seeded + broad all pass):

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_probe.py --config-path config/config.yaml --source all --limit 5 --max-terms 3 --require-all
```

Probe only seeded terms with override:

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_probe.py --source seeded --seed-term "ai agents"
```

Run post-rollout verification (live probe + DB evidence + report presence):

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_rollout_verify.py \
  --config-path config/config.yaml \
  --db-path /var/lib/universal-agent/csi/csi.db \
  --lookback-hours 24
```

### 4) Delivery health thresholds

Use source-map thresholds instead of hardcoded per-source gates:

```bash
UA_CSI_DELIVERY_SOURCE_MIN_EVENTS=youtube_channel_rss=1,reddit_discovery=1,threads_owned=0,threads_trends_seeded=0,threads_trends_broad=0,csi_analytics=0
```

Set per-Threads minimums only when you want canary/SLO checks to enforce volume.

### 5) Webhooks and publishing (hybrid-ready)

- Webhook endpoints exist and are disabled by default:
  - `GET /webhooks/threads` (verification)
  - `POST /webhooks/threads` (signed payload intake + CSI event ingest)
- Enable with:
  - `CSI_THREADS_WEBHOOK_ENABLED=1`
  - `THREADS_WEBHOOK_VERIFY_TOKEN=<token>`
- POST ingest behavior when enabled:
  - validates `x-hub-signature-256` using `THREADS_APP_SECRET`
  - normalizes webhook changes into `threads_owned` events
  - dedupes against polling (`threads:{media_id}` dedupe key)
  - stores + emits via normal CSI delivery path
  - records webhook ingest telemetry in `source_state` key `threads_webhook:state`
- Smoke test helper:

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_webhook_smoke.py \
  --base-url "http://127.0.0.1:8091" \
  --verify \
  --ingest
```
- Stage-3 webhook canary helper (stable media id for dedupe-safe repeated checks):

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_webhook_smoke.py \
  --base-url "https://app.clearspringcg.com" \
  --verify \
  --ingest \
  --fixed-media-id \
  --write-json /opt/universal_agent/artifacts/csi/threads_webhook_canary_verify/latest.json
```
- Systemd units:
  - `deployment/systemd/csi-threads-webhook-canary-verify.service`
  - `deployment/systemd/csi-threads-webhook-canary-verify.timer`
- Publishing interface is implemented as a disabled contract:
  - `create_container`
  - `publish_container`
  - `reply_to_post`
  - Gate: `CSI_THREADS_PUBLISHING_ENABLED=1` (phase 2)
  - Governance env knobs:
    - `CSI_THREADS_PUBLISH_DRY_RUN` (default `1`)
    - `CSI_THREADS_PUBLISH_APPROVAL_MODE` (`manual_confirm` or `autonomous`)
    - `CSI_THREADS_PUBLISH_MAX_DAILY_POSTS` (default `5`)
    - `CSI_THREADS_PUBLISH_MAX_DAILY_REPLIES` (default `10`)
    - `CSI_THREADS_PUBLISH_STATE_PATH` (daily cap state file)
    - `CSI_THREADS_PUBLISH_AUDIT_PATH` (JSONL audit trail)

Phase-2 smoke helper (defaults to dry-run, requires approval id in manual mode):

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_publish_smoke.py \
  --config-path config/config.yaml \
  --operation create \
  --media-type TEXT \
  --text "CSI phase 2 dry-run canary post" \
  --approval-id "threads-phase2-canary-001" \
  --audit-actor "threads-rollout-bot" \
  --audit-reason "phase2 dry-run canary"
```

Phase-2 preflight gate is enabled by default in the smoke script. Before any
write call, it checks:

1. Required env vars (`THREADS_APP_ID`, `THREADS_APP_SECRET`,
   `THREADS_USER_ID`, `THREADS_ACCESS_TOKEN`)
2. Token validity + scopes via `graph.facebook.com/debug_token`
3. Threads `/me` identity match (`THREADS_USER_ID`)
4. Fallback non-destructive write-capability probe when `debug_token` is unavailable
5. Audit fields supported in smoke payload:
   - `--audit-actor`
   - `--audit-reason`

Default required scopes for live canary:

- `threads_basic`
- `threads_content_publish`

If scope verification is not possible in your environment, you can override:

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_publish_smoke.py \
  --allow-unverified-scopes
```

Phase-2 canary audit verification helper (reads JSONL audit trail):

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_publish_canary_verify.py \
  --audit-path /var/lib/universal-agent/csi/threads_publishing_audit.jsonl \
  --lookback-hours 48 \
  --min-records 1 \
  --max-error-rate 0.60 \
  --write-json /opt/universal_agent/artifacts/csi/threads_publish_canary_verify/latest.json
```

Systemd units for ongoing canary health checks:

- `deployment/systemd/csi-threads-publish-canary-verify.service`
- `deployment/systemd/csi-threads-publish-canary-verify.timer`

## UA ↔ CSI Analyst Task Protocol

CSI ingester API now exposes task endpoints for delegating analysis work into CSI:

- `POST /analysis/tasks` create task
- `GET /analysis/tasks` list tasks (optional `status`, `request_type`, `limit`, `offset`)
- `GET /analysis/tasks/{task_id}` get one task
- `POST /analysis/tasks/{task_id}/cancel` cancel pending/running task

Example create payload:

```json
{
  "request_type": "category_deep_dive",
  "priority": 80,
  "request_source": "ua",
  "payload": {
    "category": "ai",
    "lookback_hours": 72,
    "limit": 200
  }
}
```

Task runner executes queued tasks and emits `analysis_task_completed` / `analysis_task_failed` events back to UA.

Task bootstrap keeps baseline recurring tasks in queue (`trend_followup`, `category_deep_dive`, `channel_deep_dive`) so the CSI analyst loop stays active even without manual task submissions.

## Tailnet Residential Transcript Worker

Use this when VPS transcript extraction is blocked by YouTube cloud-IP anti-bot controls.

1. Run a transcript worker endpoint on a residential Tailnet node (same API path):
   - `http://<residential-tailnet-ip>:8002/api/v1/youtube/ingest`
2. Configure CSI endpoint failover in `deployment/systemd/csi-ingester.env`:

```bash
CSI_RSS_ANALYSIS_TRANSCRIPT_ENDPOINTS=http://<residential-tailnet-ip>:8002/api/v1/youtube/ingest,http://127.0.0.1:8002/api/v1/youtube/ingest
```

Or use helper script on VPS:

```bash
/opt/universal_agent/CSI_Ingester/development/scripts/csi_set_transcript_endpoints.sh http://<residential-tailnet-ip>:8002/api/v1/youtube/ingest
```

This helper updates both:

- `CSI_RSS_ANALYSIS_TRANSCRIPT_ENDPOINTS` in `csi-ingester.env`
- `UA_HOOKS_YOUTUBE_INGEST_URLS` in `/opt/universal_agent/.env`
- and preserves gateway-readable `.env` permissions (`root:ua`, mode `640`) when `ua` exists.

3. Restart CSI and validate:

```bash
systemctl restart csi-ingester
systemctl start csi-rss-semantic-enrich.service
journalctl -u csi-rss-semantic-enrich.service -n 80 --no-pager
```

Notes:

- Endpoints are tried left-to-right until one returns a transcript.
- `rss_event_analysis.analysis_json` stores endpoint attempts for troubleshooting.
- `transcript_ref` includes endpoint host, e.g. `youtube_transcript_api@100.95.187.38:8002`.
- Shared adapter module for other blocked network actions:
  - `csi_ingester/net/egress_adapter.py`
  - functions: `parse_endpoint_list`, `detect_anti_bot_block`, `post_json_with_failover`.
- If runtime services fail after env edits, run:
  - `scripts/vpsctl.sh doctor`
  - `scripts/vpsctl.sh fix-perms`

## Structure

- `csi_ingester/`: runtime package
- `config/`: config examples
- `scripts/`: operational utilities
- `tests/`: unit, contract, integration tests

Key runtime notes:

- SQLite schema is migration-based (`schema_migrations` table).
- Adapter checkpoint/seed state is persisted in `source_state` for restart-safe behavior.
- Reddit source onboarding scaffold exists as `csi_ingester/adapters/reddit_discovery.py` and is disabled by default (`sources.reddit_discovery.enabled=false`).
