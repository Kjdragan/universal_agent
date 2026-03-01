# CSI Rebuild Status

Last updated: 2026-03-01 23:30 America/Chicago
Status owner: Codex

Handoff reference: `docs/csi-rebuild/06_packet_handoff.md`
Post-packet roadmap: `docs/csi-rebuild/07_post_packet10_work_phases.md`

## Program State
- Phase: 2 (reliability autopilot)
- Overall: In progress
- Main branch readiness: Complete

## Current Objectives
1. Keep packet 13 daily SLO gatekeeper signals stable in production.
2. Validate packet 13 timer/service rollout and daily state updates on VPS.
3. Move to packet 14 artifact discoverability and traceability.
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
| Phase 1 reliability changes | Done | Packets 6-10 delivered and validated. |
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
| Canary-aware operator panel (packet 11) | Done | Added dedicated delivery-health regression/recovery notification kinds + metadata passthrough for runbook actions. |
| Guarded auto-remediation runner (packet 12) | Done | Added `csi_delivery_health_auto_remediate.py` + timer/service + guardrail tests and notification wiring. |
| Reliability SLO gatekeeper (packet 13) | Done | Added `csi_delivery_slo_gatekeeper.py`, daily timer/service, SLO breach/recovery ingest wiring, API endpoint, and CSI dashboard SLO panel. |
| Artifact discoverability & traceability (packet 14) | Done | Normalized traceability metadata (source, session_key, report_key, artifact_paths) on all CSI ingest-path notifications. Added Open Report/Session/Artifact action buttons and fallback explanation in CSI dashboard notification detail. Added metadata completeness test. |
| Session rehydrate reliability (packet 15) | Done | Added checkpoint diagnostics (age, tasks, artifacts, original_request) and rehydrate readiness assessment to session detail API. Added rehydrate indicator UI in sessions panel with structured fallback explanation. Added 3 API tests for checkpoint/run_log+memory/empty-history variants. |
| Research quality scoring v1 (packet 16) | Done | Added `csi_quality_score.py` with 4-dimension scorer (evidence coverage, novelty, source diversity, actionability). Wired quality score into CSI notification metadata. Added quality grade badge in notification list + detailed breakdown in notification detail panel. 20 unit tests + integration assertions pass. |
| UA<->CSI follow-up contract v2 (packet 17) | Done | Added `csi_followup_contract.py` with explicit request/response schema, correlation IDs, hard budget (max 10) and timeout (60s-86400s) enforcement. Wired correlation_id into gateway specialist loop follow-up path. 24 contract tests pass. |
| Iterative refinement policy engine (packet 18) | Done | Added `csi_refinement_policy.py` with deterministic 5-outcome policy (close_loop, request_followup, escalate, budget_exhausted, suppressed). Priority chain: suppressed > close > budget_exhausted > escalate > followup. 20 policy table + simulation tests pass. |
| High-signal publishing pipeline (packet 19) | Done | Added `csi_publish_filter.py` with 4-tier classification (critical, high_value, digest, suppressed), quality/anomaly gating, and executive digest builder. 24 suppression/dedupe + digest output tests pass. |
| Source coverage expansion controls (packet 20) | Done | Added `csi_source_coverage.py` with per-source quotas, watchlist sharding, feature-flag scaffold (X/Threads/Bluesky/HN), backpressure detection with progressive throttling, starvation prevention, and quota enforcement. 30 tests pass. |
| Operations governance pack (packet 21) | Done | Added 5 runbooks: incident triage, remediation escalation, rollback, data repair, and on-call quick commands with expected outputs and "what good looks like" criteria. |

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
- `tests/gateway/test_signals_ingest_endpoint.py` (packet 14): 20 passed.
- `tests/gateway/test_ops_api.py -k rehydrate` (packet 15): 3 passed.
- `tests/unit/test_csi_quality_score.py` (packet 16): 20 passed.
- `tests/gateway/test_signals_ingest_endpoint.py` (packet 16): 20 passed.
- `tests/unit/test_csi_followup_contract.py` (packet 17): 24 passed.
- `tests/gateway/test_signals_ingest_endpoint.py` (packet 17): 20 passed.
- `tests/unit/test_csi_refinement_policy.py` (packet 18): 20 passed.
- `tests/unit/test_csi_publish_filter.py` (packet 19): 24 passed.
- `tests/unit/test_csi_source_coverage.py` (packet 20): 30 passed.
- `tests/gateway/test_ops_api.py -k dashboard_csi_delivery_health_reports_source_and_adapter_state`: 1 passed.
- `CSI_Ingester/development/tests/unit/test_csi_delivery_slo_gatekeeper.py`: 2 passed.
- `tests/gateway/test_signals_ingest_endpoint.py -k reliability_slo or auto_remediation_failed or delivery_health_regression or delivery_health_recovered`: 5 passed.
- `tests/gateway/test_ops_api.py -k dashboard_csi_reliability_slo_reads_latest_state or dashboard_csi_delivery_health_reports_source_and_adapter_state`: 2 passed.
- `npm --prefix web-ui run build`: passed.

## Open Risks
- Monitor that new runtime-generated artifacts do not reintroduce panel noise.
- Validate deploy/runtime state after mainline consolidation.

## Next Execution Step
- Implement packet 22: RC soak + GA gate.
