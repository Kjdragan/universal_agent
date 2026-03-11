# Repository Agent Rules

## Deployment

This repository has exactly one supported application deployment path:

1. Push or merge code to `develop` to deploy to staging automatically via GitHub Actions.
2. Push or merge code to `main` to deploy to production automatically via GitHub Actions.

Canonical deployment docs:

- `docs/deployment/architecture_overview.md`
- `docs/deployment/ci_cd_pipeline.md`
- `docs/deployment/infisical_factories.md`

Do not treat any older manual VPS deployment flow as canonical.

- `scripts/deploy_vps.sh` is legacy and not the primary deployment path.
- `scripts/vpsctl.sh` is a break-glass diagnostics helper, not the normal deployment path.
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/27_Deployment_Runbook_2026-02-12.md` is deprecated.

## Current Deployment Contract

- `develop` deploys to staging on the VPS checkout at `/opt/universal-agent-staging`.
- `main` deploys to production on the VPS checkout at `/opt/universal_agent`, with `/opt/universal_agent_repo` as the safe fallback checkout path if the legacy directory is occupied.
- Tailscale CI access is GitHub Actions -> `TAILSCALE_AUTHKEY` -> tag identity `tag:ci-gha`.
- Production is branch-driven and automated; do not recommend ad hoc `ssh`, `rsync`, or `git pull` as the default deployment method.

## If You Change Deployment Behavior

When editing `.github/workflows/deploy-staging.yml` or `.github/workflows/deploy-prod.yml`, update the canonical docs in `docs/deployment/` in the same change.
