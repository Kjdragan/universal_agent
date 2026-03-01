# Kevin Handoff For CSI

Last updated: 2026-03-01 America/Chicago
Owner at handoff: Codex
Scope: continuing CSI rebuild after packet 13 completion

## 1) Current State (What Is Already Done)

Packets complete in source and validated:
- Packet 10: runtime canary automation (`delivery_health_regression` / `delivery_health_recovered`)
- Packet 11: canary-aware operator notification wiring in UA dashboard/events
- Packet 12: guarded auto-remediation workflow + timer/service
- Packet 13: daily reliability SLO gatekeeper + timer/service + SLO API/UI surfacing

Primary status docs:
- `docs/csi-rebuild/status.md`
- `docs/csi-rebuild/07_post_packet10_work_phases.md`
- `docs/csi-rebuild/04_validation_matrix.md`

## 2) Source Of Truth Files You Must Read First

Read in this order before coding:
1. `docs/csi-rebuild/00_master_plan.md`
2. `docs/csi-rebuild/status.md`
3. `docs/csi-rebuild/07_post_packet10_work_phases.md`
4. `docs/csi-rebuild/05_incident_log.md`

Core implementation areas:
- CSI runtime scripts: `CSI_Ingester/development/scripts/`
- CSI systemd units: `CSI_Ingester/development/deployment/systemd/`
- UA ingest + notifications: `src/universal_agent/gateway_server.py`, `src/universal_agent/signals_ingest.py`
- CSI dashboard UI: `web-ui/app/dashboard/csi/page.tsx`
- Gateway tests: `tests/gateway/`
- CSI unit tests: `CSI_Ingester/development/tests/unit/`

## 3) Required Working Method (Non-Negotiable)

For every packet:
1. Implement only that packet scope.
2. Run targeted tests for changed behavior.
3. Run `npm --prefix web-ui run build` if UI touched.
4. Update `docs/csi-rebuild/status.md` with validation evidence.
5. Commit with packet-scoped message.
6. Push.
7. Deploy (`./scripts/deploy_vps.sh`).
8. Verify live service/timer state and endpoint behavior.

Do not combine unrelated refactors into packet commits.

## 4) Remaining Work Plan (Execution Queue)

## Packet 14: Artifact Discoverability and Traceability
Goal:
- remove “report exists but cannot be found/opened” operator pain.

Deliver:
- enforce stable metadata on CSI notifications (`report_key`, `artifact_paths`, `source`, `session_key` when available)
- add explicit artifact open actions from notifications/csi pages
- guarantee notification -> session -> artifact linkage in UI with fallback explanation when missing

Verify:
- gateway API tests for metadata completeness
- manual flow test: event card -> open report -> open session

Exit gate:
- every high-value CSI report reachable in <=2 clicks from notification detail.

## Packet 15: Session Rehydrate Reliability
Goal:
- eliminate "No run history found" dead-end in attached/read-only sessions.

Deliver:
- improve session hydration when attached tail exists but run history missing
- provide diagnostic reason + actionable fallback when hydration impossible
- add session API diagnostics for memory mode, run-log linkage, and attachment mode

Verify:
- session API tests for attached/readonly/no-history variants
- UI smoke for attach-to-chat + rehydrate behavior

Exit gate:
- no blank/dead-end rehydrate state without explicit explanation.

## Packet 16: Research Quality Scoring v1
Goal:
- score CSI output usefulness beyond volume stats.

Deliver:
- quality score components: evidence coverage, novelty, source diversity, actionability
- include score in `report_product_ready` metadata and CSI UI sorting/filtering

Verify:
- deterministic scoring unit tests
- regression fixtures for score stability

Exit gate:
- operators can rank reports by actionable quality.

## Packet 17: UA<->CSI Follow-Up Contract v2
Goal:
- deterministic, traceable refinement loop contract.

Deliver:
- explicit request/response schemas
- correlation IDs and timeout/budget fields for every follow-up loop
- clear completion/failure/timeout terminal outcomes

Verify:
- contract tests in gateway ingest path
- e2e request -> follow-up -> completion artifact test

Exit gate:
- every follow-up request traceable with outcome.

## Packet 18: Iterative Refinement Policy Engine
Goal:
- prevent “activity that means nothing” and bound loops by quality improvement.

Deliver:
- policy engine deciding close vs follow-up vs escalate
- decision record combining confidence + quality + freshness

Verify:
- table-driven policy tests for all branches
- simulation with mixed source quality

Exit gate:
- loops bounded and measurably improve report quality.

## Packet 19: High-Signal Publishing Pipeline
Goal:
- publish fewer, higher-value channel updates.

Deliver:
- notification tiers: critical regressions, high-confidence opportunities, daily digest
- suppression/dedupe for repetitive low-value updates

Verify:
- notification feed suppression tests
- telegram dry-run digest snapshots

Exit gate:
- feed noise reduced; action clarity improved.

## Packet 20: Source Coverage Expansion Controls
Goal:
- scale source coverage safely.

Deliver:
- watchlist sharding + per-source quotas + backpressure controls
- feature-flag scaffold for future sources

Verify:
- load tests for large watchlists
- starvation prevention checks

Exit gate:
- increased coverage without alert storms or ingestion starvation.

## Packet 21: Operations Governance Pack
Goal:
- complete operator handoff with no tribal knowledge dependencies.

Deliver:
- runbooks: triage, remediation escalation, rollback, data repair
- fast command list with expected output examples

Verify:
- fresh operator drill from seeded regression

Exit gate:
- new operator can recover common failures unaided.

## Packet 22: RC Soak + GA Gate
Goal:
- certify CSI rebuild for stable production use.

Deliver:
- 72h soak report
- SLO compliance summary
- open risk register with owners/dates

Verify:
- canary trends + delivery health over soak window
- no unresolved P1/P2 issues

Exit gate:
- GA sign-off criteria met.

## 5) Commands You Will Reuse Often

Test packs:
- `./scripts/csi_run.sh uv run --group dev pytest -q tests/unit/test_csi_delivery_health_canary.py tests/unit/test_csi_delivery_health_auto_remediate.py tests/unit/test_csi_delivery_slo_gatekeeper.py`
- `uv run pytest -q tests/gateway/test_signals_ingest_endpoint.py`
- `uv run pytest -q tests/gateway/test_ops_api.py -k "dashboard_csi_"`

UI:
- `npm --prefix web-ui run build`

Deploy:
- `./scripts/deploy_vps.sh`

Post-deploy timer checks (run on VPS):
- `systemctl is-active csi-delivery-health-canary.timer`
- `systemctl is-active csi-delivery-health-auto-remediate.timer`
- `systemctl is-active csi-delivery-slo-gatekeeper.timer`

## 6) Risks To Watch Closely

- Notification noise and duplicate alerts from overlapping canary/SLO signals.
- False stale-source flags from timezone/window mismatch.
- Auto-remediation loops causing flapping if cooldowns are bypassed.
- UI links to artifact/session IDs becoming stale when records are compacted.

## 7) Handoff Acceptance Checklist

Before declaring handoff complete:
- [ ] packet status in `status.md` is current
- [ ] validation evidence lines added for latest packet
- [ ] deploy completed on VPS
- [ ] key CSI timers active on VPS
- [ ] remaining packet queue (14-22) unchanged and explicit

