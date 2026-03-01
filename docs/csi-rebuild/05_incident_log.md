# CSI Incident Log

Last updated: 2026-03-01

Use this file for production-like incident entries during rebuild rollout.

## Entry Template
- Timestamp (UTC):
- Area:
- Symptom:
- Impact:
- Root cause:
- Fix implemented:
- Verification evidence:
- Follow-up prevention:

## Active/Recent Entries

### 2026-03-01 - Telegram stream silence (RSS/Reddit)
- Area: CSI digest delivery
- Symptom: Telegram channels only showed quality alerts, no regular content updates.
- Impact: Perceived CSI inactivity and low trust.
- Root cause: digest `last_sent_id` persisted ahead of current DB max ID; zero new rows selected.
- Fix implemented: cursor auto-heal reset when cursor exceeds max ID.
- Verification evidence: unit tests added (`test_digest_cursor_recovery.py`) and digest script output confirms reset path.
- Follow-up prevention: add cursor reset metric and delivery-health endpoint visibility.

### 2026-03-01 - Adapter single-source failure could stall effective RSS/Reddit ingestion
- Area: CSI data-plane ingestion and delivery
- Symptom: prolonged RSS/Reddit silence with quality alerts only; hard to distinguish source fetch failures from real low activity.
- Impact: low operator trust and delayed root-cause isolation.
- Root cause: adapter polling could fail on one source response and lose cycle visibility; no adapter-level health state surfaced to dashboard.
- Fix implemented:
  - RSS + Reddit adapters now isolate per-source fetch/parse failures (continue polling other sources).
  - CSI service now persists `adapter_health:*` source-state snapshots each poll cycle.
  - Added `/api/v1/dashboard/csi/delivery-health` for per-source ingest/delivery/DLQ + adapter health.
  - Added `scripts/csi_validate_live_flow.py` for strict RSS/Reddit checks and optional live smoke verification.
- Verification evidence:
  - `tests/unit/test_youtube_rss_adapter.py` (channel failure isolation test)
  - `tests/unit/test_reddit_discovery_adapter.py` (subreddit failure isolation + endpoint fallback test)
  - `tests/unit/test_service_flow.py` (adapter health + emit-disabled DLQ behavior)
  - `tests/gateway/test_ops_api.py::test_dashboard_csi_delivery_health_reports_source_and_adapter_state`
- Follow-up prevention: wire delivery-health endpoint into CSI dashboard next (packet 9) with clear source-level repair actions.
