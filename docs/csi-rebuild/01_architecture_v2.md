# CSI Architecture v2

Last updated: 2026-03-01

## System Layers
1. Ingestion adapters
- `youtube_channel_rss`
- `reddit_discovery`
- (future) `threads_discovery`

2. Normalization and storage
- Canonical event envelope
- Durable event store + delivery state + DLQ

3. Enrichment
- Transcript/media enrichment (when available)
- Category/entity/theme extraction
- Source quality metadata

4. Synthesis
- Narrative report generation
- Ranked opportunity bundle generation

5. Delivery and orchestration
- UA ingest events
- Telegram stream digests (RSS, Reddit, Tutorial isolated)
- Trend-specialist bounded follow-up loop

## Routing Boundaries (Hard Requirements)
- `youtube_playlist` -> tutorial pipeline only
- `youtube_channel_rss` + `reddit_discovery` -> CSI trend pipeline only
- No implicit fallback between these lanes

## Reliability Controls
- Cursor self-heal when `last_sent_id > max(id)`
- Delivery attempts table
- Scheduled DLQ replay with bounded retries
- Failure classification (`network`, `auth`, `upstream_5xx`, `timeout`, `unknown`)

