# CSI Rebuild Packet Handoff (1-10, Historical Snapshot)

Last updated: 2026-03-01 17:28 America/Chicago  
Owner: Codex  
Purpose: low-context transfer document so a new developer can continue immediately.

> Note: this document is retained as a historical checkpoint from the packet-10 era.
> Active handoff state is now tracked in:
> - `docs/csi-rebuild/status.md`
> - `docs/csi-rebuild/07_post_packet10_work_phases.md`

## Current State (At A Glance)
- Branch: `main`
- Remote: `origin/main` at `577bdc1`
- Packet status: `1-13 complete` (see `status.md` for live progression).
- CSI working tree: use `git status` for current mutable state; this file no longer tracks it.

## Packet Matrix
| Packet | Objective | Status | Evidence |
|---|---|---|---|
| 1 | Establish CSI rebuild program baseline docs + source-control hygiene | Complete | `fa017cb` (`docs/csi-rebuild/*`, cleanup baseline) |
| 2 | Reliability foundation setup (delivery tracking, replay pattern, routing guardrails) | Complete | `457eb34`, `d7d634f`, `f20e4ae` |
| 3 | Output contract upgrade (trend + opportunities) | Complete | `8ccb8ba` |
| 4 | VPS deployment + runtime rollout verification | Complete | Recorded in [status.md](/home/kjdragan/lrepos/universal_agent/docs/csi-rebuild/status.md) progress board (“VPS deployment (packet 4)”) |
| 5 | Specialist confidence/orchestration baseline | Complete | `8c6ee88`, `96a9683`, `6a47173` |
| 6 | Reliability packet continuation (loop triage and remediation controls) | Complete | `6a47173`; API + UI loop actions/triage/cleanup |
| 7 | Specialist quality guardrails and health surfacing | Complete | `96a9683`; stale/low-signal/confidence-drift alerts |
| 8 | Close CSI data-plane gap (RSS/Reddit ingestion health + delivery path repair + live flow validation) | Complete (uncommitted in current tree) | Modified CSI adapters/service, added `/api/v1/dashboard/csi/delivery-health`, added `scripts/csi_validate_live_flow.py`; tests passed (17 + 12) |
| 9 | Production threshold tuning + operator UX wiring for delivery-health + source-level repair hints | Complete (uncommitted in current tree) | Gateway tuning params + UI repair hints/runbook copy; build/lint passed |
| 10 | Runtime canary automation for delivery-health regressions (auto-alert + guided remediation workflow) | In progress | Design/integration points identified; implementation pending |

## Packet 8/9 Work Already Implemented Locally (Pending Commit)
- Backend:
  - [service.py](/home/kjdragan/lrepos/universal_agent/CSI_Ingester/development/csi_ingester/service.py)
  - [youtube_channel_rss.py](/home/kjdragan/lrepos/universal_agent/CSI_Ingester/development/csi_ingester/adapters/youtube_channel_rss.py)
  - [reddit_discovery.py](/home/kjdragan/lrepos/universal_agent/CSI_Ingester/development/csi_ingester/adapters/reddit_discovery.py)
  - [gateway_server.py](/home/kjdragan/lrepos/universal_agent/src/universal_agent/gateway_server.py)
  - [csi_validate_live_flow.py](/home/kjdragan/lrepos/universal_agent/CSI_Ingester/development/scripts/csi_validate_live_flow.py)
- UI:
  - [page.tsx](/home/kjdragan/lrepos/universal_agent/web-ui/app/dashboard/csi/page.tsx)
- Tests/Docs:
  - [test_service_flow.py](/home/kjdragan/lrepos/universal_agent/CSI_Ingester/development/tests/unit/test_service_flow.py)
  - [test_reddit_discovery_adapter.py](/home/kjdragan/lrepos/universal_agent/CSI_Ingester/development/tests/unit/test_reddit_discovery_adapter.py)
  - [test_youtube_rss_adapter.py](/home/kjdragan/lrepos/universal_agent/CSI_Ingester/development/tests/unit/test_youtube_rss_adapter.py)
  - [test_ops_api.py](/home/kjdragan/lrepos/universal_agent/tests/gateway/test_ops_api.py)
  - [status.md](/home/kjdragan/lrepos/universal_agent/docs/csi-rebuild/status.md), [04_validation_matrix.md](/home/kjdragan/lrepos/universal_agent/docs/csi-rebuild/04_validation_matrix.md), [05_incident_log.md](/home/kjdragan/lrepos/universal_agent/docs/csi-rebuild/05_incident_log.md)

## Validation Evidence (Latest Local)
- `uv run pytest -q tests/gateway/test_ops_api.py -k "dashboard_csi_delivery_health_reports_source_and_adapter_state or dashboard_csi_"` -> 12 passed
- `scripts/csi_run.sh uv run --group dev pytest tests/unit/test_service_flow.py tests/unit/test_reddit_discovery_adapter.py tests/unit/test_youtube_rss_adapter.py -q` -> 17 passed
- `npm --prefix web-ui run build` -> passed
- `npm --prefix web-ui run lint` -> passed (one existing non-CSI warning in `ExplorerPanel.tsx`)

## Packet 10 Implementation Plan (Ready To Execute)
1. Add canary script:
   - `CSI_Ingester/development/scripts/csi_delivery_health_canary.py`
   - Inputs: CSI DB path, window, thresholds, cooldown, remediation profile.
   - Behavior: evaluate delivery-health regression/recovery, persist canary state, emit `csi_analytics` events:
     - `delivery_health_regression`
     - `delivery_health_recovered`
   - Include actionable remediation hints and runbook commands in event `subject`.
2. Add runtime scheduler:
   - `CSI_Ingester/development/deployment/systemd/csi-delivery-health-canary.service`
   - `CSI_Ingester/development/deployment/systemd/csi-delivery-health-canary.timer`
   - Wire both into `csi_install_systemd_extras.sh`.
3. Wire ingest-side notifications:
   - Extend CSI event policy/handler in [gateway_server.py](/home/kjdragan/lrepos/universal_agent/src/universal_agent/gateway_server.py) so canary regression events:
     - are high-priority,
     - require action,
     - preserve remediation metadata/runbook command for operator actions.
4. Add tests:
   - CSI unit tests for canary state transitions (ok -> regression, regression -> recovered, cooldown).
   - Gateway tests for notification severity/requires_action + metadata passthrough.
5. Validate end-to-end:
   - unit tests + gateway tests,
   - run canary once in dry-run and forced regression mode,
   - verify notification appears with repair guidance.

## Current Uncommitted Files (Before Packet 10)
```
M  CSI_Ingester/development/README.md
M  CSI_Ingester/development/csi_ingester/adapters/reddit_discovery.py
M  CSI_Ingester/development/csi_ingester/adapters/youtube_channel_rss.py
M  CSI_Ingester/development/csi_ingester/service.py
M  CSI_Ingester/development/tests/unit/test_reddit_discovery_adapter.py
M  CSI_Ingester/development/tests/unit/test_service_flow.py
M  CSI_Ingester/development/tests/unit/test_youtube_rss_adapter.py
M  docs/csi-rebuild/02_interfaces_and_schemas.md
M  docs/csi-rebuild/04_validation_matrix.md
M  docs/csi-rebuild/05_incident_log.md
M  docs/csi-rebuild/status.md
M  src/universal_agent/gateway_server.py
M  tests/gateway/test_ops_api.py
M  web-ui/app/dashboard/csi/page.tsx
?? CSI_Ingester/development/scripts/csi_validate_live_flow.py
?? corporation/docs/006_MASTER_IMPLEMENTATION_PLAN.md
?? corporation/docs/phases/
?? corporation/status.md
```

## Handoff Execution Checklist
1. Review and commit packet 8/9 local changes first.
2. Deploy packet 8/9 to VPS and verify `/api/v1/dashboard/csi/delivery-health`.
3. Implement packet 10 exactly as above.
4. Run packet 10 tests + service timer verification.
5. Deploy packet 10 and confirm:
   - regression event auto-alerts with remediation guidance,
   - recovery event clears/updates status cleanly.
