# 22. VPS Remote Dev, Deploy, and File Transfer Runbook (Archived 2026-02-11)

## Archived

This runbook previously described manual SSH, `scp`, and direct VPS helper flows.

It is no longer the supported application deployment path.

## Current Deployment Contract

1. Push or merge to `develop` for the automated staging deploy.
2. Push or merge to `main` for the automated production deploy.

Canonical references:

- `AGENTS.md`
- `docs/deployment/ci_cd_pipeline.md`
- `docs/deployment/architecture_overview.md`

## Break-Glass Only

- `scripts/vpsctl.sh` may still be used for narrowly targeted diagnostics or emergency intervention.
- Manual `ssh`, `scp`, and `rsync` flows are not the default recommendation for deploying repository changes.

If this file is reached during normal development, stop and use the GitHub Actions deployment path instead.
