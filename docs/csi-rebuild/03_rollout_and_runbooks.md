# CSI Rollout and Runbooks

Last updated: 2026-03-01

## Current Deployment Rule

This repo now deploys through GitHub Actions.

- Push or merge to `develop` for the automated staging deploy.
- Push or merge to `main` for the automated production deploy.
- Do not use `./scripts/deploy_vps.sh` as the default deploy path for CSI work.

## Branch Hygiene Runbook
1. Keep a recovery branch before cleanup.
2. Stage only intended changes.
3. Shelve untracked/experimental noise.
4. Commit and push feature branch.
5. Fast-forward `main` from validated branch.
6. Push `main` and verify clean working tree.

## Deploy Runbook
1. Push validated branch state.
2. Promote to `develop` for staging or to `main` for production.
3. Wait for the corresponding GitHub Actions deploy workflow to complete successfully.
4. Verify CSI timers/services on the target host.
5. Run targeted smoke tests.
6. Validate Telegram stream activity and CSI health API.

## Rollback Trigger
- sustained delivery failures,
- silent opportunity output with active ingestion,
- major dashboard regression.

## Rollback Action
1. Revert last deployment commit set.
2. Push the revert to the active deployment branch so GitHub Actions redeploys it.
3. Re-run smoke tests and health checks.
