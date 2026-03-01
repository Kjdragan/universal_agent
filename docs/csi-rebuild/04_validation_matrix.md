# CSI Validation Matrix

Last updated: 2026-03-01

## Unit Coverage
- Cursor recovery for RSS/Reddit digest state.
- Routing invariants (playlist vs trend lanes).
- Delivery failure classification.
- Confidence method selection and scoring logic.
- Ranked opportunity deterministic ordering.

## Integration Coverage
- End-to-end ingest -> enrich -> synthesize -> emit -> dashboard path.
- DLQ replay success and failure paths.
- Notification dismissal/delete behavior in tutorial panel.

## Operational Canaries (VPS)
- RSS Telegram receives non-alert content when RSS events exist.
- Reddit Telegram receives non-alert content when Reddit events exist.
- DLQ backlog trend is stable/declining.
- Overnight continuity checks reflect observed runs correctly.

## Acceptance Metrics
- `undelivered_last_24h <= 2%`
- `dlq_replay_success_60m >= 95%`
- narrative + ranked bundle emitted in active windows

