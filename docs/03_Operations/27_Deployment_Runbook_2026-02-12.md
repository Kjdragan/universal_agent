# 27. Universal Agent Deployment Runbook (2026-02-14)

## Deprecated

This runbook is no longer the canonical deployment path.

Current supported deployment contract:

1. Push or merge to `develop` to deploy staging automatically via GitHub Actions.
2. Push or merge to `main` to deploy production automatically via GitHub Actions.

Canonical references:

- `docs/deployment/architecture_overview.md`
- `docs/deployment/ci_cd_pipeline.md`
- `AGENTS.md`

Legacy scripts such as `scripts/deploy_vps.sh` and `scripts/vpsctl.sh` should be treated as break-glass helpers only, not the normal deployment process.
