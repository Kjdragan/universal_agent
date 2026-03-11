# CI/CD Automated Pipelines

This file is retained for continuity, but the canonical automated pipeline docs now live in:

- `docs/deployment/ci_cd_pipeline.md`
- `docs/deployment/architecture_overview.md`

Current supported automation:

1. GitHub Actions deploys `develop` to staging automatically.
2. GitHub Actions deploys `main` to production automatically.
3. Tailscale access is non-interactive and tag-based.
4. Manual VPS deployment scripts are legacy helpers, not the primary deploy contract.
