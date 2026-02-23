# 74. Unified Creator Signal Intelligence Strategy (2026-02-22)

## 1. Objective

Define one reusable capability for creator and trend signals (YouTube first, then Reddit/X/Threads) so we stop building one-off trigger flows and instead route all sources through a single ingestion, normalization, and action model.

This document answers:

1. Should this be a skill, an agent, or a background service?
2. What can we reliably get from YouTube RSS/API/Composio?
3. How do we trigger internal processes (including YouTube tutorial explainer) consistently?
4. Should this live inside Universal Agent (UA), outside UA, or hybrid?

## 2. Core Product Intent

### 2.1 Immediate use cases

1. Playlist curation trigger:
   1. When a video is added to a specific playlist, run the YouTube tutorial explainer flow.
2. Creator watchlist trigger:
   1. Maintain a list of target channels.
   2. Detect new uploads quickly.
   3. Generate short analysis notes and include them in daily reporting (for example, 7:00 AM summary).

### 2.2 Near-term expansion

1. Add Reddit/X/Threads signal feeds.
2. Run cross-source trend detection (topic momentum, repeated themes, notable creator moves).
3. Notify Simone/VP General and optionally auto-open follow-up tasks.

## 3. Facts And Constraints

1. YouTube does not provide a webhook for playlist-item additions.
2. ✅ **Composio YouTube playlist triggers are validated and working** (as of 2026-02-22):
   - Polling interval: ~1 minute (configurable)
   - Webhook delivery: Reliable with HMAC signature verification
   - End-to-end latency: ~1-2 minutes from playlist add to agent trigger
   - **For complete setup instructions, see Document 75.**
3. YouTube channel RSS is available and low-friction for public channel upload detection.
4. Playlist RSS is inconsistent and may return `404` for many playlists (observed in our environment), so playlist automation cannot depend on RSS alone.
5. YouTube Data API `playlistItems.list` costs 1 quota unit per call and is practical for controlled polling.
6. Existing UA ingress and transforms already support a stable hook-based action path:
   1. `POST /api/v1/hooks/composio`
   2. `POST /api/v1/hooks/youtube/manual`
   3. `webhook_transforms/composio_youtube_transform.py`
   4. `webhook_transforms/manual_youtube_transform.py`
7. Existing local poller scaffolding exists and can be generalized:
   1. `scripts/youtube_playlist_poll_to_manual_hook.py`
   2. `scripts/run_youtube_playlist_poller.sh`
   3. `deployment/systemd/universal-agent-youtube-playlist-poller.timer`

## 4. Capability Model (What We Should Build Once)

Treat this as one platform capability called `Creator Signal Intelligence` with four layers.

### 4.1 Layer A: Source Adapters (ingestion)

1. YouTube playlist watcher (API polling for private/unlisted/public playlists).
2. YouTube channel watcher (RSS and/or Data API).
3. Composio adapter (managed account + OAuth + trigger routing where useful).
4. Reddit adapter.
5. X adapter.
6. Threads adapter.

### 4.2 Layer B: Signal Normalization (single contract)

Every source emits a common event envelope, then all downstream automation becomes source-agnostic.

```json
{
  "event_id": "src:provider:object:timestamp-or-hash",
  "source": "youtube_playlist|youtube_channel_rss|reddit|x|threads|composio",
  "event_type": "video_added_to_playlist|channel_new_upload|post_trending|topic_spike",
  "occurred_at": "2026-02-22T06:21:29Z",
  "received_at": "2026-02-22T06:21:32Z",
  "dedupe_key": "youtube:video:dQw4w9WgXcQ",
  "subject": {
    "platform": "youtube",
    "video_id": "dQw4w9WgXcQ",
    "channel_id": "UC...",
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "title": "..."
  },
  "routing": {
    "pipeline": "youtube_tutorial_explainer",
    "priority": "normal",
    "tags": ["ai", "watchlist", "creator"]
  },
  "raw": {}
}
```

### 4.3 Layer C: Action Router

Map normalized signals to actions:

1. `run_skill` (one-shot transformation).
2. `run_agent` (multi-step reasoning or triage).
3. `enqueue_report_item` (daily digest queue).
4. `send_notification` (Slack/Telegram/email/UI).
5. `open_task` (Todoist/internal mission).

### 4.4 Layer D: Intelligence Outputs

1. Real-time alerts.
2. Rolling briefing feed.
3. Daily 7:00 AM report section.
4. Weekly trend synthesis.

## 5. Skill Vs Agent Vs Background Service

This should not be an either-or decision.

1. Background service:
   1. Responsibility: watch external sources 24/7, dedupe, persist normalized events.
   2. Why: continuous polling/ingestion should not depend on interactive sessions.
2. Skills:
   1. Responsibility: deterministic transformation packages (example: YouTube tutorial explainer artifact generation).
   2. Why: reusable, testable workflows.
3. Agents (VP General or dedicated trend agent):
   1. Responsibility: higher-order reasoning across many normalized events, prioritization, and recommendations.
   2. Why: cross-source synthesis is contextual, not just ETL.

Conclusion: build a capability with all three roles, each with clear boundaries.

## 6. Recommended Architecture Choice

### 6.1 Option A: Keep all in UA runtime

Pros:

1. One operational surface.
2. Reuses existing hooks, transforms, and session handling.

Cons:

1. More load and operational coupling in UA.
2. Harder to isolate ingestion failures from agent runtime.

### 6.2 Option B: Separate Trend App on VPS

Pros:

1. Cleaner isolation and simpler scaling.
2. Can evolve independently from core UA workflows.

Cons:

1. Additional deployment and observability surface.
2. Requires clear API contract into UA/Simone.

### 6.3 Option C: Hybrid (recommended)

1. Keep ingestion watchers as a lightweight dedicated service.
2. Route normalized events to UA hook/API boundary for skill/agent execution.
3. Keep VP General as the analysis consumer, not the ingestion host.

Reasoning:

1. Avoids overloading UA with persistent polling loops.
2. Keeps your core intelligence and artifact tooling in UA.
3. Lets you add/remove sources without touching explainer logic.

### 6.4 Boundary Model: `CSI Ingestor` + `UA Consumer` (recommended implementation shape)

Treat the hybrid model as two explicit systems with a strict contract.

1. `CSI Ingestor` (separate VPS service/application):
   1. Owns external source polling/subscription.
   2. Owns source auth tokens, pull cadence, rate limiting, retries.
   3. Normalizes raw source events into `Creator Signal Contract v1`.
   4. Performs source-level dedupe and writes an event log.
   5. Emits normalized events to UA ingress.
2. `UA Consumer/Analysis` (inside Universal Agent):
   1. Validates incoming normalized events.
   2. Performs action routing (`run_skill`, `run_agent`, `enqueue_report_item`, notifications).
   3. Generates artifacts, summaries, daily/weekly reporting.
   4. Performs cross-source reasoning via VP General or a dedicated trend agent.

This allows ingestion complexity to scale independently while keeping UA focused on analysis, synthesis, and action.

## 7. YouTube-Specific Design

### 7.1 Playlist trigger (your primary requirement)

Use YouTube Data API polling with OAuth/account context for the target playlist.

Flow:

1. Poll `playlistItems.list` on interval.
2. Detect new additions by `videoId` not seen before.
3. Emit normalized `video_added_to_playlist` signal.
4. Route to `youtube_tutorial_explainer` action.
5. Persist result reference for reporting.

### 7.2 Channel watchlist trigger

Use channel RSS for quick low-cost detection of new uploads.

Flow:

1. Maintain watchlist of channel IDs.
2. Poll RSS endpoints per channel.
3. Emit normalized `channel_new_upload` signals.
4. Route to:
   1. quick summary path, and/or
   2. full explainer path based on routing rules.

### 7.3 Building watchlist from a seed playlist

1. Read playlist items.
2. Extract unique channel IDs.
3. Materialize these channel IDs into watchlist table.
4. Optionally require minimum quality score before auto-follow.

## 8. What Data You Get From RSS Without Further Processing

For YouTube channel RSS, directly available fields include:

1. `entry.id` (includes video identity).
2. `yt:videoId`.
3. `yt:channelId`.
4. `title`.
5. `link` (watch URL).
6. `published`.
7. `updated`.
8. `author.name`.

Useful immediate actions without extra API calls:

1. "New from watched creator" alerts.
2. Upload cadence tracking per channel.
3. Time-window counts (last 24h, 7d).
4. Daily summary lists with direct links.
5. Basic trend hints from title keywords over time.

## 9. Additional High-Value Use Cases From The Same Capability

1. Competitor launch radar:
   1. Detect bursts of videos/posts on a shared topic.
2. Topic momentum:
   1. Cross-source spike detection (YouTube + Reddit + X).
3. Creator delta watch:
   1. Identify when a watched creator changes focus or posting frequency.
4. Idea mining:
   1. Auto-cluster new content into "themes worth testing".
5. R and D backlog seeding:
   1. Open tasks from repeated high-signal topics.
6. Intelligence quality scoring:
   1. Rank events by source credibility, recency, and engagement proxy.

## 10. Triggering Processes In UA (Hook-Based Answer)

Yes, a hook-based approach should remain the entry point into UA automation.

Recommended trigger contract:

1. External watcher posts normalized payload to UA ingress endpoint (existing hooks or dedicated `/api/v1/signals/ingest`).
2. UA validates auth/signature and dedupe key.
3. UA router maps event type to action profile.
4. Action profile dispatches either:
   1. skill execution,
   2. VP General mission,
   3. report queue insertion,
   4. notification only.

This keeps trigger infrastructure independent from specific skill logic.

## 11. Quota And Cost Strategy

### 11.1 YouTube Data API

1. `playlistItems.list` is 1 unit per request.
2. Poll every 60s for one playlist: 1,440 units/day.
3. Poll every 300s for one playlist: 288 units/day.
4. Use adaptive polling:
   1. faster while user is actively curating,
   2. slower during idle periods.

### 11.2 RSS

1. No YouTube Data API quota cost.
2. Still rate-limit and respect backoff.
3. Use conditional requests (`ETag`/`If-Modified-Since`) where available.

### 11.3 Practical recommendation

1. Playlist trigger uses API polling (reliable for private/public playlists).
2. Channel watchlist uses RSS first, API fallback only when needed.
3. Keep global daily quota budget and per-source rate caps in config.

## 12. Operational Guardrails

1. Idempotency by stable `dedupe_key` across all sources.
2. Replay-safe signature verification on inbound hooks.
3. Dead-letter queue for malformed events.
4. Secrets hygiene:
   1. rotate any key exposed in logs/chat,
   2. use env-managed secrets only.
5. Observability:
   1. ingestion success rate,
   2. latency source->summary,
   3. dedupe hit rate,
   4. per-source error rates,
   5. quota burn projection.

## 13. Integration Into Daily 7:00 AM Report

1. Persist normalized events and summary artifacts in a queryable store.
2. At report time, build sections:
   1. New uploads from watched creators (last 24h).
   2. Top 5 high-signal summaries.
   3. Cross-source emerging themes.
   4. Suggested actions.
3. Include links to stored summary artifacts for drill-down.

## 14. Delivery Plan (Phased)

### Phase 0: Platform foundation (before source expansion)

1. Ship `Creator Signal Contract v1`.
2. Define `CSI Ingestor -> UA Consumer` API contract and auth model.
3. Implement shared dedupe/idempotency conventions.
4. Implement shared observability and dead-letter handling.

### Phase 1: YouTube foundation (now)

1. Finalize playlist polling watcher using Data API.
2. Keep existing manual/composio transforms as action entry points.
3. Add event normalization + dedupe store.

### Phase 2: YouTube watchlist intelligence

1. Add channel RSS watcher service.
2. Add watchlist management model (seed from playlist + manual edits).
3. Route new uploads to quick-summary pipeline.

### Phase 3: Reporting and prioritization

1. Add daily report assembler from normalized event store.
2. Add importance scoring and suppression rules.

### Phase 4: Reddit adapter using the same four-layer pattern

1. Implement Reddit source adapter in `CSI Ingestor`.
2. Normalize to the same contract (`post_published`, `post_trending`, `topic_spike`).
3. Reuse the same UA action router and reporting pipeline.
4. Add source-specific scoring and noise suppression rules.

### Phase 4 Status Update (2026-02-23)

1. Reddit source adapter scaffold is live with watchlist-file loading and canary enable/disable workflow.
2. Reddit now has dedicated delivery surfaces:
   1. `csi-reddit-telegram-digest.timer` for feed notifications.
   2. `csi-reddit-trend-report.timer` for UA analytics event ingestion (`reddit_trend_report`).
3. Telegram stream separation is explicit at env level:
   1. `CSI_RSS_TELEGRAM_CHAT_ID` (YouTube RSS feed digest),
   2. `CSI_REDDIT_TELEGRAM_CHAT_ID` (Reddit feed digest),
   3. `CSI_TUTORIAL_TELEGRAM_CHAT_ID` (playlist tutorial run updates + artifact links/paths).
4. Telegram routing supports forum-topic partitioning per stream via:
   1. `CSI_RSS_TELEGRAM_THREAD_ID`,
   2. `CSI_REDDIT_TELEGRAM_THREAD_ID`,
   3. `CSI_TUTORIAL_TELEGRAM_THREAD_ID`.
5. Reddit and playlist tutorial digest services run strict stream routing mode by default (`--strict-stream-routing`) to prevent accidental cross-posting into the RSS/default stream when stream-specific routing is unset.

### Phase 5: Threads adapter using the same four-layer pattern

1. Implement Threads source adapter in `CSI Ingestor`.
2. Normalize to the same contract and reuse existing action/router/report layers.
3. Add source-specific quotas/limits and reliability handling.

### Phase 6: Broader multi-source trend synthesis

1. Add X adapter.
2. Run cross-source trend synthesis via VP General or dedicated trend agent.
3. Add "trend intelligence" outputs (alerts, daily digest, weekly synthesis, idea backlog seeds).

## 15. Decision Summary

1. Do not rely on playlist RSS for core automation.
2. Use both approaches, but with separation of responsibilities:
   1. YouTube playlist: API polling.
   2. YouTube channel watchlist: RSS-first.
3. Keep hook-based internal triggering as the common control plane.
4. Treat this as a platform capability, not a one-off skill.
5. Implement hybrid architecture:
   1. ingestion service outside core UA runtime,
   2. UA as processing and intelligence engine.
6. Expand source coverage by cloning adapters into the same contract and routing stack, not by building source-specific pipelines.

## 16. Suggested Next Build Ticket Set

1. `CSI Ingestor Service Skeleton v1` (service runtime + config + health + metrics).
2. `Creator Signal Contract v1` (schema + validation + dedupe + versioning).
3. `CSI->UA Ingest API v1` (auth/signature + dead-letter + replay handling).
4. `YouTube Playlist Adapter v1` (API polling + OAuth + normalized emit).
5. `YouTube Channel RSS Adapter v1` (watchlist + normalized emit).
6. `Signal Router v1` (event type -> skill/agent/report action mapping).
7. `Daily Intelligence Digest v1` (7:00 AM report section generator).
8. `Reddit Adapter v1` (same four-layer integration path).
9. `Threads Adapter v1` (same four-layer integration path).
10. `Trend Agent v1` (cross-source clustering and recommendation loop).

## 17. Source Onboarding Blueprint (Use For Every New Source)

For each new source (YouTube, Reddit, Threads, X, etc.), implement in this order and do not skip layers:

1. Layer A (`Source Adapter`):
   1. Define source auth, rate limits, polling/subscription semantics, and retry/backoff.
2. Layer B (`Normalization`):
   1. Map source payloads into `Creator Signal Contract v1`.
   2. Define source-specific `event_type` mappings and `dedupe_key`.
3. Layer C (`Action Router`):
   1. Configure route profiles without creating source-specific business logic forks.
4. Layer D (`Outputs`):
   1. Ensure source events appear in the same reporting surfaces and trend synthesis jobs.

Definition of done for a new source:

1. Ingestor can produce normalized events reliably.
2. UA can execute at least one skill/agent action from that source.
3. Events appear in daily digest and trend report outputs.
4. Source is observable with per-source SLO and error budget.

---

## Related Documentation

### Composio Integration
- **Document 75**: Composio YouTube Trigger Complete Implementation Guide
  - Step-by-step Composio trigger setup
  - Working configuration examples
  - Complete troubleshooting guide
  - Template for any Composio service trigger

### Infrastructure & Deployment
- **Document 18**: Hostinger VPS Composio Webhook Deployment Runbook
- **Document 42**: Hybrid Local VPS Webhook Operations Source Of Truth

### Ingestion & Processing
- **Document 45**: YouTube Webhook Robustness And Gemini Video Analysis

### Historical
- **Document 16**: Composio Trigger Ingress And YouTube Automation Plan (Archived 2026-02-22)
  - Initial implementation plan, superseded by Document 75 validation
