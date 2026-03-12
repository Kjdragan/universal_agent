# CI/CD Automated Pipelines

This file is retained for continuity, but the canonical automated pipeline docs now live in:

- `docs/deployment/ci_cd_pipeline.md`
- `docs/deployment/architecture_overview.md`

Current supported automation:

1. GitHub Actions runs Codex review on pull requests into `develop`.
2. GitHub Actions deploys `develop` to staging automatically after merge.
3. GitHub Actions promotes an exact validated `develop` SHA to `main` via a manual promotion workflow.
4. GitHub Actions deploys `main` to production automatically after promotion.
5. Tailscale access is non-interactive and tag-based.
6. Manual VPS deployment scripts are legacy helpers, not the primary deploy contract.
