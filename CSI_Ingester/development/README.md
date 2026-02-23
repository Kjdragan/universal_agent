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

If Telegram chat routing is not configured yet (`CSI_RSS_TELEGRAM_CHAT_ID` unset), the digest job now exits cleanly in "skipped" mode instead of failing the systemd service.
Digest format includes category sections (`AI`, `Non-AI`, `Unknown`) using `rss_event_analysis.category` when available.

Install periodic systemd jobs on VPS (requires root):

```bash
/opt/universal_agent/CSI_Ingester/development/scripts/csi_install_systemd_extras.sh
```

Timers installed:

- `csi-rss-telegram-digest.timer` -> every 10 minutes (sends one batched Telegram digest when new RSS events exist)
- `csi-rss-semantic-enrich.timer` -> every 10 minutes at `:02` (transcript extraction + ai/non_ai semantic summary)
- `csi-rss-trend-report.timer` -> hourly at minute `:12` (aggregated trend report event to UA)
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

Run RSS trend report manually:

```bash
scripts/csi_run.sh python3 scripts/csi_rss_trend_report.py --db-path /path/to/csi.db --window-hours 24 --force
```

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
