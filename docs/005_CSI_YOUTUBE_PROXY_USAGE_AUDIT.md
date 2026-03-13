# CSI YouTube Proxy Usage Audit

Date: 2026-03-04  
Author: Codex

## 1. Why this audit exists

You asked for a concrete answer to:

1. Where residential proxy bandwidth is being used.
2. Whether that usage is tied to CSI YouTube RSS/watchlist flow.
3. What data is actually fetched (transcript vs metadata vs video bytes).
4. Whether CSI is turning that source data into useful outputs, or mostly just stats.

This report is based on:

1. Current code paths in this repo.
2. Live VPS runtime config/state checks.
3. Live SQLite queries against `/opt/universal_agent/CSI_Ingester/development/var/csi.db`.

---

## 2. Direct answers first

## Runtime note (2026-03-12)

The tutorial pipeline has since been normalized to a VPS-primary topology:

1. VPS is the default host for playlist watching, transcript ingestion, artifact generation, and tutorial repo bootstrap.
2. `UA_HOOKS_YOUTUBE_INGEST_URLS` should prefer VPS loopback first, typically `http://127.0.0.1:8002/api/v1/youtube/ingest`.
3. Local workstation tutorial processing is now a dev-only fallback rather than the normal runtime path.
4. Terminal hook ingest failures now persist `local_ingest_result.json`, and proxy CONNECT/tunnel failures are classified as `proxy_connect_failed`.
5. The current default Webshare residential endpoint is `p.webshare.io:80`. The older `proxy.webshare.io:80` host is stale for this path and can surface as `Tunnel connection failed: 404 Not Found`.

## Is residential proxy used for YouTube RSS feed polling?
No. RSS polling itself is direct HTTP to YouTube feeds (`videos.xml`) and does not use Webshare proxy.

Code:

1. `CSI_Ingester/development/csi_ingester/adapters/youtube_channel_rss.py:47`
2. `CSI_Ingester/development/csi_ingester/adapters/youtube_channel_rss.py:203`

## Is residential proxy used for transcript fetch?
Yes. Transcript fetch and metadata fetch run through the local ingest endpoint path, which uses Webshare credentials when present.

Code:

1. `src/universal_agent/gateway_server.py:9123`
2. `src/universal_agent/youtube_ingest.py:139`
3. `src/universal_agent/youtube_ingest.py:326`

## Is video content downloaded?
No in CSI path. The ingest path fetches:

1. Transcript text (`youtube_transcript_api`)
2. Metadata (`yt-dlp --no download` behavior via API)

It does not fetch/store full video bytes.

Code:

1. `src/universal_agent/youtube_ingest.py:231`
2. `src/universal_agent/youtube_ingest.py:175`
3. `src/universal_agent/youtube_ingest.py:195`

## Is proxy use only for watchlist/tutorial?
Not exactly.

1. CSI watchlist RSS enrichment uses transcript endpoint -> proxy-backed.
2. Hook-routed YouTube agent flows (`youtube-expert`) also use local ingest worker when enabled.
3. Separate non-CSI `tgtg` module can also consume the same proxy credentials if active.

Code:

1. `CSI_Ingester/development/scripts/csi_rss_semantic_enrich.py:383`
2. `src/universal_agent/hooks_service.py:1108`
3. `src/universal_agent/tgtg/config.py:67`

---

## 3. Current architecture and where proxy spend occurs

## A. CSI YouTube watchlist path

1. Poll RSS watchlist channels (metadata-only):
   - source: `youtube_channel_rss`
   - output: event with `video_id`, `title`, `channel`, URL
2. Semantic enrich picks delivered RSS events and calls transcript ingest endpoint.
3. Ingest endpoint runs transcript + metadata extraction in parallel.
4. Enrich script classifies/summarizes (Claude when enabled) and stores structured analysis.
5. Trend/insight/report-product scripts aggregate that analysis into downstream events/artifacts.

Proxy spend happens at step 3.

## B. Hook / user-query YouTube path

When hook action routes to `youtube-expert` and `UA_HOOKS_YOUTUBE_INGEST_MODE=local_worker`, hooks service pre-fetches transcript via the same ingest endpoint. This is also proxy-backed.

Code:

1. `src/universal_agent/hooks_service.py:1437`
2. `src/universal_agent/hooks_service.py:1520`

## C. Non-CSI shared proxy consumer (potential)

`tgtg` module falls back to `PROXY_USERNAME` / `PROXY_PASSWORD` if `TGTG_PROXIES` is unset.

Code:

1. `src/universal_agent/tgtg/config.py:51`
2. `src/universal_agent/tgtg/config.py:67`

---

## 4. What CSI does with YouTube source material

## Input from RSS

RSS event contains metadata only:

1. `video_id`
2. `channel_id` / `channel_name`
3. `title`
4. `url`
5. `published_at`

No transcript and no video bytes at this stage.

Code:

1. `CSI_Ingester/development/csi_ingester/adapters/youtube_channel_rss.py:233`

## Input from ingest endpoint

Ingest returns:

1. `transcript_text`
2. `transcript_chars`
3. metadata fields (`title`, `channel`, `duration`, etc)
4. quality/failure metadata

Code:

1. `src/universal_agent/youtube_ingest.py:406`

## Storage behavior (important)

CSI does **not** persist full transcript text in `rss_event_analysis`. It stores:

1. `transcript_status`
2. `transcript_chars`
3. `transcript_ref`
4. `category`
5. `summary_text`
6. analysis JSON + token usage

Code:

1. `CSI_Ingester/development/csi_ingester/store/sqlite.py:71`
2. `CSI_Ingester/development/scripts/csi_rss_semantic_enrich.py:236`

## Downstream use

The stored summary/category/themes are used by:

1. `csi_rss_trend_report.py` -> `rss_trend_report` events + markdown
2. `csi_rss_insight_analyst.py` -> `rss_insight_*` events + markdown
3. `csi_analysis_task_runner.py` -> follow-up analyst responses
4. `csi_report_product_finalize.py` -> hourly report product + opportunity bundle artifacts/events

Code:

1. `CSI_Ingester/development/scripts/csi_rss_trend_report.py:106`
2. `CSI_Ingester/development/scripts/csi_rss_trend_report.py:555`
3. `CSI_Ingester/development/scripts/csi_rss_insight_analyst.py:146`
4. `CSI_Ingester/development/scripts/csi_rss_insight_analyst.py:422`
5. `CSI_Ingester/development/scripts/csi_report_product_finalize.py:536`
6. `CSI_Ingester/development/scripts/csi_report_product_finalize.py:617`

---

## 5. Live VPS snapshot (current state)

Historical snapshot from the earlier workstation-first configuration:

1. `/opt/universal_agent/.env:UA_HOOKS_YOUTUBE_INGEST_MODE=local_worker`
2. `/opt/universal_agent/.env:UA_HOOKS_YOUTUBE_INGEST_URLS=http://100.95.187.38:8002/api/v1/youtube/ingest,http://127.0.0.1:8002/api/v1/youtube/ingest`
3. `/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env:CSI_RSS_ANALYSIS_TRANSCRIPT_ENDPOINTS=...same order...`

Current target runtime contract:

1. VPS tutorial ingest should run against `http://127.0.0.1:8002/api/v1/youtube/ingest` first.
2. Tailnet/self-IP tutorial ingest endpoints are fallback-only and should not be the first hop on VPS.
3. Tutorial repo bootstrap defaults to the VPS target root `UA_TUTORIAL_BOOTSTRAP_TARGET_ROOT=<UA_ARTIFACTS_DIR>/tutorial_repos`.

Timer cadence (live):

1. `csi-rss-semantic-enrich.timer` every 10m
2. `csi-rss-trend-report.timer` hourly at `:12`
3. `csi-rss-insight-analyst.timer` hourly at `:22`

### 24h event/source mix (current DB)

1. `threads_trends_broad`: 30
2. `youtube_channel_rss`: 9
3. `csi_analytics`: 5
4. `reddit_discovery`: 4
5. `csi_analyst`: 3
6. `threads_owned`: 1
7. `threads_trends_seeded`: 1

### 24h transcript/enrichment volume

1. `rss_event_analysis rows`: 6
2. `transcript_ok`: 6 (100%)
3. `total transcript chars`: 29,658
4. `avg chars/transcript`: 4,943
5. endpoint host split: `100.95.187.38:8002` only

### 7d in current DB

1. same as 24h (rows_7d=6, chars_7d=29,658)
2. category mix: `ai=3`, `other_interest=2`, `political=1`

### Important caveat

Current DB history starts around `2026-03-04 19:01 UTC`:

1. `events_min_created=2026-03-04 19:01:58`
2. `analysis_min=2026-03-04 19:12:11`

So this snapshot does **not** represent normal multi-day baseline yet.

---

## 6. Why you currently see “stats” more than narrative

You are not wrong. The system does generate markdown narrative fields, but the dominant surfaced UX has been numeric panels/events. The natural-language outputs exist in:

1. trend report markdown (`rss_trend_report.subject.markdown`)
2. insight report markdown (`rss_insight_*.subject.markdown`)
3. report product markdown artifacts under `/opt/universal_agent/artifacts/csi-reports/<day>/product/`

If those are not surfaced prominently in UI/email flows, you mainly see counters and health metrics.

---

## 7. Cost/value assessment right now

## What appears efficient

1. Proxy spend is currently concentrated on transcript fetch attempts only.
2. RSS polling itself is cheap (metadata only, no proxy).
3. Ingest path does not download full video.

## What appears weak / risky

1. Full transcript text is not persisted in CSI DB, only derived summary/category.
2. Value surfacing is weak: narrative outputs are generated but under-exposed versus operational metrics.
3. Shared proxy credentials can be consumed by non-CSI module (`tgtg`) if active.
4. Deployment currently rsyncs repo `CSI_Ingester/development/var/` to VPS, which can overwrite runtime DB state and distort longitudinal analytics.

Evidence for (4): deploy script excludes `artifacts/` and others, but not `CSI_Ingester/development/var/`:

1. `scripts/deploy_vps.sh:124`

---

## 8. Recommendations (priority order)

## P0 (immediate, same day)

1. Protect runtime DB from deploy overwrite:
   - add rsync exclude for `CSI_Ingester/development/var/`
2. Add dashboard panel for latest markdown narrative artifacts:
   - trend markdown

## 9. Current operator checks

For the current VPS-primary tutorial pipeline:

1. Run `uv run python scripts/check_youtube_ingress_readiness.py --json` for config-only readiness.
2. Run `uv run python scripts/check_youtube_ingress_readiness.py --probe-video-id <public_video_id> --json` for a real ingest probe.
3. Treat `proxy_connect_failed` as a proxy transport/config incident distinct from `proxy_auth_failed` and `proxy_quota_or_billing`.
4. For failed hook ingest runs, inspect `local_ingest_result.json` in the run workspace before relying only on journal logs.
   - insight markdown
   - report product markdown
3. Keep proxy-alert notifications enabled (already implemented):
   - `youtube_ingest_proxy_alert` for quota/auth failures

## P1 (next 2-3 days)

1. Add explicit proxy-usage metrics table (hourly):
   - transcript requests
   - success ratio
   - total transcript chars
   - endpoint host mix
2. Split “input vs output value” metrics:
   - videos detected (RSS)
   - transcripts fetched
   - summaries produced
   - items appearing in trend/insight markdown
   - opportunities emitted

## P2 (next week)

1. Cost-control policy tiers:
   - high-priority channels: full transcript
   - medium: transcript on keyword/velocity trigger
   - low: metadata-only unless escalated
2. Optional transcript cache retention (compressed artifact store) for re-analysis without refetch.
3. Separate proxy credentials by subsystem (CSI vs TGTG) to avoid accidental shared spend.

---

## 9. Bottom line

You are not wasting money on RSS polling itself; that part is metadata-only and cheap.  
Residential proxy cost is primarily tied to transcript ingestion. Those transcripts are being used for classification/summaries and fed into trend/insight/report pipelines, but the “narrative product” is currently under-surfaced compared to operational metrics.  

The highest-impact fixes are:

1. preserve CSI DB across deploys,
2. surface narrative markdown outputs directly in primary UI,
3. add explicit proxy spend-to-value instrumentation.
