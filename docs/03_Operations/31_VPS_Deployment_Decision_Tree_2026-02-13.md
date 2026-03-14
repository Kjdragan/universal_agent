# 31. VPS Deployment Decision Tree (Archived 2026-02-14)

## Archived

This document previously described manual VPS deployment choices.

It is no longer the current deployment contract.

## Current Deployment Contract

1. Push or merge to `develop` to deploy to staging automatically via GitHub Actions.
2. Push or merge to `main` to deploy to production automatically via GitHub Actions.

Canonical references:

- `AGENTS.md`
- `docs/deployment/ci_cd_pipeline.md`
- `docs/deployment/architecture_overview.md`

## Legacy Script Status

- `scripts/deploy_vps.sh` is legacy and not the default deployment path.
- `scripts/vpsctl.sh` is break-glass tooling for narrowly targeted diagnostics or emergency intervention.

If you are looking for the current answer to "how do I deploy this change?", the answer is:

1. validate locally
2. commit and push
3. promote to `develop` for staging verification
4. promote to `main` only for intentional production rollout
