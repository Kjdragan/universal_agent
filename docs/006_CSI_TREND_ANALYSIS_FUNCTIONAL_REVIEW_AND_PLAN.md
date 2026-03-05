# CSI Trend Analysis Functional Review And Improvement Plan

Date: 2026-03-04  
Author: Codex

## 1. Executive Summary

You are right to push on this. CSI currently has strong ingestion activity but weak user-facing narrative surfacing.

Direct answers:

1. Trend analysis does exist and creates natural-language markdown outputs.
2. Those outputs are generated in backend tables/events/artifacts, but are not consistently surfaced in your main UA experience.
3. YouTube semantic enrichment does use transcript text with LLMs, but only a bounded excerpt window (`12000` chars to LLM prompt) and short summary target (`<=700` prompt target, stored up to `1000`).
4. Themes and confidence are generated and stored in `analysis_json`; they are real, not placeholders.
5. Your DB-history concern is valid: deploy sync currently includes `CSI_Ingester/development/var/` and can overwrite VPS runtime DB state.

Bottom line: the pipeline is partially functional, but the product experience (natural-language insight delivery and persistence safety) is below your intent.

---

## 2. What Each Process Does (Simple Terms)

You mentioned: CSI timer/cadence, RSS, semantic enricher, RSS trend report, RSS insight analyst.

### A) `csi-ingester` service (continuous poll loop)

What it does: continuously polls enabled source adapters at their configured intervals and emits source events.

Code:

1. `CSI_Ingester/development/csi_ingester/service.py:40`
2. `CSI_Ingester/development/csi_ingester/service.py:57`

Configured source cadence:

1. YouTube RSS every 5m (`300s`)  
2. Reddit discovery every 5m (`300s`)  
3. Threads owned every 15m (`900s`)  
4. Threads seeded trends every 15m (`900s`)  
5. Threads broad trends every 30m (`1800s`)

Config:

1. `CSI_Ingester/development/config/config.yaml:14`
2. `CSI_Ingester/development/config/config.yaml:19`
3. `CSI_Ingester/development/config/config.yaml:28`
4. `CSI_Ingester/development/config/config.yaml:52`
5. `CSI_Ingester/development/config/config.yaml:71`

### B) `csi-rss-semantic-enrich.timer` (every 10m)

What it does: for delivered YouTube RSS events, fetch transcript/metadata, run classification/summarization, store analysis rows.

Outputs created:

1. `rss_event_analysis` row per analyzed RSS event.
2. `analysis_json` with themes/confidence/category.

Code:

1. `CSI_Ingester/development/scripts/csi_rss_semantic_enrich.py:220`
2. `CSI_Ingester/development/scripts/csi_rss_semantic_enrich.py:495`

### C) `csi-rss-trend-report.timer` (hourly at `:12`)

What it does: aggregates analyzed RSS rows into a trend report (counts + channels + themes + movers + markdown), stores it, emits event to UA.

Outputs created:

1. `trend_reports` row with `report_markdown` and `report_json`.
2. `csi_analytics` event type `rss_trend_report` (includes markdown in subject).

Code:

1. `CSI_Ingester/development/scripts/csi_rss_trend_report.py:97`
2. `CSI_Ingester/development/scripts/csi_rss_trend_report.py:407`
3. `CSI_Ingester/development/scripts/csi_rss_trend_report.py:555`

### D) `csi-rss-insight-analyst.timer` (hourly at `:22`)

What it does: generates two higher-level reports (emerging + daily), including movers/themes/actions, stores + emits.

Outputs created:

1. `insight_reports` rows (`report_type=emerging|daily`).
2. `csi_analytics` events `rss_insight_emerging` / `rss_insight_daily` with markdown.

Code:

1. `CSI_Ingester/development/scripts/csi_rss_insight_analyst.py:540`
2. `CSI_Ingester/development/scripts/csi_rss_insight_analyst.py:422`

### E) `csi-report-product-finalize.timer` (hourly at `:35`)

What it does: consolidates latest reports into product artifacts and readiness events.

Outputs created:

1. Markdown/JSON artifacts under `/opt/universal_agent/artifacts/csi-reports/...`.
2. `report_product_ready` + `opportunity_bundle_ready` events.

Code:

1. `CSI_Ingester/development/scripts/csi_report_product_finalize.py:485`
2. `CSI_Ingester/development/scripts/csi_report_product_finalize.py:621`
3. `CSI_Ingester/development/scripts/csi_report_product_finalize.py:653`

Timer list reference:

1. `CSI_Ingester/development/README.md:109`

---

## 3. What “Trend Analysis” Actually Produces Today

### Source-level events

From adapters, CSI emits raw source events (Threads/Reddit/YouTube RSS metadata hits) into `events`.

Threads-specific event types include:

1. `threads_keyword_hit` (seeded/broad)
2. `threads_trend_snapshot` (seeded/broad)
3. `threads_post_observed`, `threads_reply_observed`, `threads_mention_observed` (owned)

Code:

1. `CSI_Ingester/development/csi_ingester/adapters/threads_trends_seeded.py:139`
2. `CSI_Ingester/development/csi_ingester/adapters/threads_trends_seeded.py:156`
3. `CSI_Ingester/development/csi_ingester/adapters/threads_trends_broad.py:198`
4. `CSI_Ingester/development/csi_ingester/adapters/threads_owned.py:85`

### YouTube semantic layer

For YouTube RSS, semantic enrich adds:

1. category
2. summary text
3. themes list
4. confidence
5. transcript status/count/ref

Stored in:

1. `rss_event_analysis.summary_text`
2. `rss_event_analysis.analysis_json`

Schema:

1. `CSI_Ingester/development/csi_ingester/store/sqlite.py:71`

### Report layer (natural language exists here)

Natural-language markdown is produced by trend/insight scripts and also embedded into event payloads.

Code:

1. `CSI_Ingester/development/scripts/csi_rss_trend_report.py:575`
2. `CSI_Ingester/development/scripts/csi_rss_insight_analyst.py:451`

---

## 4. Your Questions About Transcript Depth, Themes, Confidence

## Are we processing transcript with LLM or just showing 700 chars?

Yes, LLM processing happens when Claude is enabled.

1. LLM prompt includes transcript excerpt up to `12000` chars.
2. Prompt asks for JSON: `category`, `summary`, `themes`, `confidence`.

Code:

1. `CSI_Ingester/development/scripts/csi_rss_semantic_enrich.py:164`
2. `CSI_Ingester/development/scripts/csi_rss_semantic_enrich.py:169`

## Is 700 chars the actual storage cap?

Not exactly.

1. Prompt asks model for summary `<=700`.
2. Storage keeps up to `1000` chars from model output.

Code:

1. `CSI_Ingester/development/scripts/csi_rss_semantic_enrich.py:169`
2. `CSI_Ingester/development/scripts/csi_rss_semantic_enrich.py:486`

## Are themes/confidence retained?

Yes.

1. `analysis_json["themes"]`
2. `analysis_json["confidence"]`

Code:

1. `CSI_Ingester/development/scripts/csi_rss_semantic_enrich.py:497`
2. `CSI_Ingester/development/scripts/csi_rss_semantic_enrich.py:498`

## Are they used downstream?

Yes.

1. Trend report parses themes from `analysis_json`.
2. Insight analyst parses themes from `analysis_json`.

Code:

1. `CSI_Ingester/development/scripts/csi_rss_trend_report.py:145`
2. `CSI_Ingester/development/scripts/csi_rss_insight_analyst.py:167`

## Should summary size be increased toward ~2000?

My recommendation: yes, for your use case.

Current setup is optimized for short operational digests, not strategic narrative depth. Raising summary budget (and downstream sample clipping) is reasonable and likely aligned with what you want.

---

## 5. Database Output Snapshot (Formatted)

## A) Current local workspace DB (this repo checkout)

From `CSI_Ingester/development/var/csi.db` right now:

1. `events`: 0 rows
2. `rss_event_analysis`: 0 rows
3. `trend_reports`: 0 rows
4. `insight_reports`: 0 rows

This means your local repo DB is not currently carrying live production history.

## B) Last known live VPS snapshot from prior verified audit (same day)

From `docs/005_CSI_YOUTUBE_PROXY_USAGE_AUDIT.md` live query capture:

1. Source mix (24h):
   - `threads_trends_broad`: 30
   - `youtube_channel_rss`: 9
   - `csi_analytics`: 5
   - `reddit_discovery`: 4
   - `csi_analyst`: 3
   - `threads_owned`: 1
   - `threads_trends_seeded`: 1
2. RSS analysis (24h):
   - rows: 6
   - transcript_ok: 6
   - transcript chars total: 29,658
   - avg chars: 4,943

Reference:

1. `docs/005_CSI_YOUTUBE_PROXY_USAGE_AUDIT.md`

## C) Why I am not claiming a fresh VPS snapshot in this doc

I attempted fresh pull, but Tailscale SSH currently requires interactive browser check (`login.tailscale.com/...`) from your active session, which this run cannot complete autonomously.

---

## 6. Why DB History Is Short (And Why You Keep Feeling “Reset”)

Your suspicion is correct.

Deploy sync currently rsyncs the repo to VPS and does not exclude `CSI_Ingester/development/var/`.

So local `var/csi.db` can overwrite VPS `var/csi.db` on deploy.

Code:

1. `scripts/deploy_vps.sh:125`

Evidence in script:

1. `artifacts/` is excluded.
2. `tmp/` is excluded.
3. `CSI_Ingester/development/var/` is not excluded.

Code:

1. `scripts/deploy_vps.sh:130`

This is the primary persistence-risk issue to fix first.

---

## 7. Why You Don’t See Useful Natural-Language Trend Results

This is mostly a surfacing/product-path issue, not total absence of generation.

Current behavior:

1. Natural-language markdown exists in DB events/reports/artifacts.
2. UI and regular user workflow still emphasize counters, health, and pipeline diagnostics.
3. Threads broad/seeded trend events are in event stream, but there is no dedicated human-facing Threads narrative report equivalent to YouTube/Reddit report quality.

Result: lots of activity, limited direct human insight.

---

## 8. Recommendations (Quality-First, Trend-First UX)

## P0: Make CSI a trend product in the UI (not a health console)

1. Make `/dashboard/csi` default to a narrative-first “Trend Briefing” view:
   - cross-source executive summary (Threads + Reddit + YouTube)
   - top narratives with plain-language explanation
   - why-it-matters + confidence + evidence links
2. Promote generated markdown to first-class UI cards:
   - latest `rss_trend_report`
   - latest `rss_insight_emerging`
   - latest `rss_insight_daily`
   - upcoming `threads_trend_report` (new)
3. Keep health/ops stats, but move them to a dedicated “CSI Health” tab:
   - delivery failures, stale sources, DLQ, token usage, proxy diagnostics
   - alert-focused, not primary user-facing trend page

## P0: Fix data persistence so trend history is trustworthy

1. Exclude `CSI_Ingester/development/var/` from deploy rsync immediately.
2. Move runtime DB to durable path (example: `/var/lib/universal-agent/csi/csi.db`).
3. Add backup rotation so trend history survives redeploy and incidents.

## P1: Increase trend-analysis depth (your core quality ask)

1. Increase semantic summary budget to ~2000 chars.
2. Increase theme richness and specificity:
   - more themes (e.g., 10-12)
   - stronger normalization + de-duplication
   - confidence plus confidence rationale fields
3. Improve transcript utilization quality:
   - keep 20k ingest cap
   - replace “single head excerpt” with head/middle/tail chunk fusion before summarization
4. Upgrade trend report composition:
   - stronger synthesis paragraphs
   - contradiction/consensus detection across sources
   - explicit “what changed since last window”

## P1: Add dedicated Threads natural-language reporting

1. Implement `csi_threads_trend_report.py` timer.
2. Aggregate seeded + broad + owned signals into one markdown narrative.
3. Emit `csi_analytics` event with:
   - narrative markdown
   - top trends, movers, and confidence
   - evidence references

## P2: Continuous analyst experience

1. Add 2-hour “Global Trend Briefing” auto-product:
   - one compact report spanning all sources
   - built for decision consumption, not debugging
2. Add report-delivery options:
   - in-app briefing feed
   - AgentMail digest
   - optional Telegram summary
3. Add source-level drill-down from each narrative claim.

---

## 9. Proposed Implementation Plan

1. Trend-first UI/UX pass (P0)
   - create “Trend Briefing” main CSI view
   - move operational metrics to “CSI Health” tab
   - surface latest markdown artifacts prominently
2. Persistence hardening pass (P0)
   - deploy rsync exclude for CSI `var/`
   - migrate DB path to durable runtime storage
   - add backup + restore test
3. Depth pass for semantic analysis (P1)
   - summary limit ~2000
   - expanded themes/confidence rationale
   - chunk-fusion transcript strategy
4. Threads narrative pass (P1)
   - add threads trend report generator + timer + UI card
5. Cross-source briefing pass (P2)
   - 2-hour consolidated narrative report
   - delivery channels + drill-down links

---

## 10. Immediate Next Steps I Recommend

1. Implement P0 trend-first UI split (Trend Briefing + CSI Health tab).
2. Implement P0 persistence fix in deploy/runtime DB path.
3. Implement P1 depth upgrades (2000-char summaries + richer themes/confidence).
4. Implement P1 dedicated Threads narrative report.

If you want, I can start implementing steps 1 and 2 now, then move to 3 and 4 in sequence.
