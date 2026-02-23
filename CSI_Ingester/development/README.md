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

Run endpoint smoke against a live UA endpoint:

```bash
scripts/csi_run.sh uv run python scripts/csi_emit_smoke_event.py --require-internal-dispatch
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
- Optional per-stream Telegram forum topic IDs:
- `CSI_RSS_TELEGRAM_THREAD_ID`
- `CSI_REDDIT_TELEGRAM_THREAD_ID`
- `CSI_TUTORIAL_TELEGRAM_THREAD_ID`
- Strict stream routing controls:
- `CSI_TELEGRAM_STRICT_STREAM_ROUTING=1` (global)
- `CSI_REDDIT_TELEGRAM_STRICT_STREAM_ROUTING=1`
- `CSI_TUTORIAL_TELEGRAM_STRICT_STREAM_ROUTING=1`
- `csi-reddit-telegram-digest.service` and `csi-playlist-tutorial-digest.service` run with `--strict-stream-routing` by default, so those streams do not silently fall back into RSS/default chat routing.

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
- `csi-analysis-task-runner.timer` -> every 10 minutes at `:06` (runs UA-submitted CSI analysis tasks)
- `csi-analysis-task-bootstrap.timer` -> hourly at minute `:03` (auto-seeds baseline recurring analysis tasks)
- `csi-report-product-finalize.timer` -> hourly at minute `:35` (materializes report artifacts + emits `report_product_ready`)
- `csi-daily-summary.timer` -> daily at `00:10 UTC` (writes summary artifacts under `/opt/universal_agent/artifacts/csi-reports/<day>/`)
- `csi-hourly-token-report.timer` -> hourly at minute 05 (sends `hourly_token_usage_report` event to UA)

Run hourly token report manually:

```bash
scripts/csi_run.sh python3 scripts/csi_hourly_token_report.py --db-path /path/to/csi.db --force
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
