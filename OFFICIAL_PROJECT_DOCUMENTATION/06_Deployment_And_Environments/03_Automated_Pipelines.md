# CI/CD Automated Pipelines

This file is retained for continuity, but the canonical automated pipeline docs now live in:

- `docs/deployment/ci_cd_pipeline.md`
- `docs/deployment/architecture_overview.md`

Current supported automation:

1. GitHub Actions runs Codex review on pull requests into `develop`.
2. GitHub Actions deploys `develop` to staging automatically after merge.
3. GitHub Actions promotes an exact validated `develop` SHA to `main` via a manual promotion workflow.
4. GitHub Actions deploys `main` to production automatically after promotion.
5. Staging and production deploys write explicit stage bootstrap identity before rendering service env files.
6. Deploy workflows validate Infisical access and runtime identity instead of provisioning machine-shaped environments during deploy.
7. Tailscale access is non-interactive and tag-based.
8. Manual VPS deployment scripts are legacy helpers, not the primary deploy contract.

For the migration record that introduced this contract, see:

- `06_Deployment_And_Environments/07_Stage_Based_Infisical_And_Machine_Bootstrap_Migration_Plan_2026-03-12.md`
