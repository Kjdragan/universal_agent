# CSI Rebuild Status

Last updated: 2026-03-01 12:47 America/Chicago
Status owner: Codex

## Program State
- Phase: 1 (reliability implementation)
- Overall: In progress
- Main branch readiness: Complete

## Current Objectives
1. Begin CSI opportunity bundle contract implementation.
2. Add dashboard surfacing for delivery-target telemetry.
3. Prepare deploy verification checklist for CSI timers/services.
4. Start confidence-method refactor scaffolding.

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
| Phase 1 reliability changes | In progress | Packet 3 complete; moving to packet 4 (opportunity contract start). |

## Validation Snapshot
- `CSI_Ingester/development/tests/unit/test_digest_cursor_recovery.py`: 2 passed.
- `tests/gateway/test_ops_api.py -k dashboard_tutorial`: 7 passed.
- `CSI_Ingester/development/tests/unit/test_delivery_attempts.py`: 2 passed.
- `CSI_Ingester/development/tests/unit/test_service_flow.py`: 2 passed.
- `tests/gateway/test_ops_api.py -k dashboard_csi_health_includes_overnight_and_source_health`: 1 passed.
- `CSI_Ingester/development/tests/unit/test_csi_playlist_tutorial_digest.py`: 6 passed.

## Open Risks
- Monitor that new runtime-generated artifacts do not reintroduce panel noise.
- Validate deploy/runtime state after mainline consolidation.

## Next Execution Step
- Implement packet 4: scaffold opportunity bundle contract and persist first structured bundle records.
