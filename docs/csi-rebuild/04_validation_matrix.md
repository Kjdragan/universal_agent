# CSI Validation Matrix

Last updated: 2026-03-01 (packet 13)

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
- Adapter failure isolation (one failing RSS channel/subreddit does not abort entire poll cycle).
- Dashboard delivery-health endpoint source rollup (`/api/v1/dashboard/csi/delivery-health`).
- CSI operator UX rendering of source-level repair hints and runbook command actions.
- Runtime delivery-health canary transitions (`delivery_health_regression` + `delivery_health_recovered`) and actionable metadata passthrough.
- Guarded auto-remediation guardrails (cooldown, max-attempt window, cursor correction, action audit trail).
- Daily reliability SLO gatekeeper evaluation + breach/recovery notification flow.

## Operational Canaries (VPS)
- RSS Telegram receives non-alert content when RSS events exist.
- Reddit Telegram receives non-alert content when Reddit events exist.
- DLQ backlog trend is stable/declining.
- Overnight continuity checks reflect observed runs correctly.
- `scripts/csi_validate_live_flow.py --emit-smoke` confirms smoke events are accepted and visible in UA activity.
- `scripts/csi_delivery_health_canary.py` emits regression/recovery events with guided runbook commands.
- `scripts/csi_delivery_health_auto_remediate.py` executes guarded self-healing actions without flapping.
- `scripts/csi_delivery_slo_gatekeeper.py --day <YYYY-MM-DD>` computes daily pass/fail and emits top-3 root-cause candidates on breach.

## Acceptance Metrics
- `undelivered_last_24h <= 2%`
- `dlq_replay_success_60m >= 95%`
- narrative + ranked bundle emitted in active windows
