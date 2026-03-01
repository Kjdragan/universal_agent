# CSI Rollout and Runbooks

Last updated: 2026-03-01

## Branch Hygiene Runbook
1. Keep a recovery branch before cleanup.
2. Stage only intended changes.
3. Shelve untracked/experimental noise.
4. Commit and push feature branch.
5. Fast-forward `main` from validated branch.
6. Push `main` and verify clean working tree.

## Deploy Runbook
1. Push branch state.
2. Run `./scripts/deploy_vps.sh`.
3. Verify CSI timers/services.
4. Run targeted smoke tests.
5. Validate Telegram stream activity and CSI health API.

## Rollback Trigger
- sustained delivery failures,
- silent opportunity output with active ingestion,
- major dashboard regression.

## Rollback Action
1. Revert last deployment commit set.
2. Redeploy.
3. Re-run smoke tests and health checks.

