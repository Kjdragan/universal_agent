# CSI Rebuild Status

Last updated: 2026-03-01 15:55 America/Chicago
Status owner: Codex

Handoff reference: `docs/csi-rebuild/06_packet_handoff.md`
Post-packet roadmap: `docs/csi-rebuild/07_post_packet10_work_phases.md`

## Program State
- Phase: 1 (reliability implementation)
- Overall: In progress
- Main branch readiness: Complete

## Current Objectives
1. Tune production thresholds for CSI delivery-health scoring.
2. Surface source-level repair hints in CSI operator UX.
3. Keep live-flow validation path current (ingest + UA activity confirmation).
4. Keep deploy verification checklist current for CSI timers/services.

## Progress Board
| Workstream | State | Notes |
|---|---|---|
| Branch/worktree hygiene | Done | Main fast-forwarded to `fa017cb`; local merged branches removed. |
| Runtime noise cleanup | Done | Runtime/generated churn removed from working tree prior merge. |
| Official documentation set | Done | `docs/csi-rebuild/*.md` scaffold created. |
| Curated commit + push | Done | Commit `fa017cb` pushed to `origin/main`. |
| Main fast-forward | Done | `main` fast-forwarded and pushed. |
| Local branch cleanup | Done | Merged local branches removed; single active `main` branch. |
| Hidden outstanding stash/worktrees | Done | No extra worktrees; temporary stash dropped. |
| Delivery attempt telemetry | Done | Added `delivery_attempts` schema + persistence in service and analytics emit paths. |
| CSI health delivery visibility | Done | `/api/v1/dashboard/csi/health` now reports delivery totals and per-target status. |
| DLQ replay automation | Done | Added `csi-replay-dlq.service` + `.timer` and installer wiring. |
| Source routing invariants | Done | Added tests proving playlist digest ignores RSS source unless explicitly overridden. |
| Phase 1 reliability changes | In progress | Packet 6 + Packet 7 + Packet 8 + Packet 9 delivered; preparing packet 10 runtime canaries. |
| Opportunity bundle persistence | Done | Added `opportunity_bundles` migration + store helpers. |
| Opportunity bundle emission | Done | `csi_report_product_finalize.py` now emits `opportunity_bundle_ready` with artifacts. |
| Opportunity API/UI surfacing | Done | Added `/api/v1/dashboard/csi/opportunities` and CSI dashboard section. |
| Confidence method scaffold | Done | Added `csi_confidence.py` evidence model + heuristic fallback. |
| Specialist loop confidence persistence | Done | `csi_specialist_loops` now stores `confidence_method` and `evidence_json`. |
| Specialist loop dashboard surfacing | Done | CSI loops API/UI now show confidence method. |
| VPS deployment (packet 4) | Done | `deploy_vps.sh` successful; gateway/api/webui/telegram/vp workers active. |
| Specialist quality guardrails | Done | Added low-signal suppression, stale-evidence alerts, confidence-drift alerts with cooldown dedupe. |
| Specialist quality health summary | Done | `/api/v1/dashboard/csi/health` now returns `specialist_quality` aggregate. |
| Specialist loop operator triage | Done | Added loop action endpoint, triage automation endpoint, and stale-loop cleanup endpoint. |
| CSI loop remediation UI | Done | Added triage/cleanup controls and per-loop remediation actions in CSI dashboard panel. |
| Adapter resilience hardening (packet 8) | Done | RSS/Reddit adapters now isolate per-source failures; one failing source no longer aborts whole poll cycle. |
| Data-plane delivery health API (packet 8) | Done | Added `/api/v1/dashboard/csi/delivery-health` with per-source ingest+delivery+DLQ state and adapter health snapshots. |
| Live-flow validator (packet 8) | Done | Added `scripts/csi_validate_live_flow.py` for strict RSS/Reddit checks and optional live smoke emission+verification. |
| Delivery-health threshold tuning (packet 9) | Done | Endpoint now supports production tuning for volume/failure/DLQ thresholds and per-source status scoring. |
| Delivery-health operator hints UI (packet 9) | Done | CSI dashboard now surfaces source-level repair hints with copyable runbook commands. |
| Runtime canary automation (packet 10) | Done | Added `csi_delivery_health_canary.py`, canary systemd timer/service, and actionable CSI regression/recovery alert wiring in gateway notifications. |

## Validation Snapshot
- `CSI_Ingester/development/tests/unit/test_digest_cursor_recovery.py`: 2 passed.
- `tests/gateway/test_ops_api.py -k dashboard_tutorial`: 7 passed.
- `CSI_Ingester/development/tests/unit/test_delivery_attempts.py`: 2 passed.
- `CSI_Ingester/development/tests/unit/test_service_flow.py`: 2 passed.
- `tests/gateway/test_ops_api.py -k dashboard_csi_health_includes_overnight_and_source_health`: 1 passed.
- `CSI_Ingester/development/tests/unit/test_csi_playlist_tutorial_digest.py`: 6 passed.
- `CSI_Ingester/development/tests/unit/test_opportunity_bundles.py`: 2 passed.
- `tests/gateway/test_ops_api.py -k dashboard_csi_reports`: 5 passed.
- `tests/gateway/test_ops_api.py -k dashboard_csi_opportunities`: 2 passed.
- `CSI_Ingester/development/tests/unit/test_delivery_attempts.py ... test_service_flow.py ... test_csi_playlist_tutorial_digest.py ... test_opportunity_bundles.py`: 12 passed.
- `tests/unit/test_csi_confidence.py`: 2 passed.
- `tests/gateway/test_signals_ingest_endpoint.py -k emerging_requests_followup_and_records_loop or opportunity_bundle_uses_evidence_confidence`: 2 passed.
- `tests/gateway/test_ops_api.py -k dashboard_csi_opportunities or dashboard_csi_reports or dashboard_csi_health_includes_overnight_and_source_health`: 8 passed.
- `npm --prefix web-ui run build`: passed.
- `tests/gateway/test_signals_ingest_endpoint.py -k low_signal_suppresses_followup_and_emits_alert or stale_evidence_emits_quality_alert`: 2 passed.
- `tests/unit/test_csi_confidence.py tests/gateway/test_ops_api.py -k dashboard_csi_*`: 10 passed.
- `tests/gateway/test_ops_api.py -k csi_specialist_loop_action_unsuppress_and_followup or csi_specialist_loop_triage_applies_remediation or csi_specialist_loop_cleanup_deletes_stale_closed`: 3 passed.
- `tests/gateway/test_ops_api.py -k dashboard_csi_`: 11 passed.
- `npm --prefix web-ui run build`: passed.
- `CSI_Ingester/development/tests/unit/test_service_flow.py tests/unit/test_reddit_discovery_adapter.py tests/unit/test_youtube_rss_adapter.py`: 17 passed.
- `tests/gateway/test_ops_api.py -k dashboard_csi_delivery_health_reports_source_and_adapter_state or dashboard_csi_health_includes_overnight_and_source_health or dashboard_csi_`: 12 passed.
- `CSI_Ingester/development/tests/unit/test_delivery_attempts.py tests/unit/test_ua_emitter.py`: 5 passed.
- `tests/gateway/test_signals_ingest_endpoint.py -k emerging_requests_followup_and_records_loop or low_signal_suppresses_followup_and_emits_alert`: 2 passed.
- `tests/gateway/test_ops_api.py -k dashboard_csi_delivery_health_reports_source_and_adapter_state or dashboard_csi_`: 12 passed.
- `CSI_Ingester/development/tests/unit/test_service_flow.py tests/unit/test_reddit_discovery_adapter.py tests/unit/test_youtube_rss_adapter.py`: 17 passed.
- `npm --prefix web-ui run build`: passed.
- `npm --prefix web-ui run lint`: passed (existing warning outside CSI scope).
- `CSI_Ingester/development/tests/unit/test_csi_delivery_health_canary.py`: 3 passed.
- `tests/gateway/test_signals_ingest_endpoint.py`: 16 passed.
- `tests/gateway/test_ops_api.py -k dashboard_csi_delivery_health_reports_source_and_adapter_state`: 1 passed.

## Open Risks
- Monitor that new runtime-generated artifacts do not reintroduce panel noise.
- Validate deploy/runtime state after mainline consolidation.

## Next Execution Step
- Implement packet 11: canary-aware operator panel (dedicated cards/actions for delivery-health regression and recovery events).
