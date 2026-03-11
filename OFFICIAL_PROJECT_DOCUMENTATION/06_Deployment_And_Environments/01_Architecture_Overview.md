# Deployment Architecture Overview

This file is retained for continuity, but the canonical deployment docs now live in:

- `docs/deployment/architecture_overview.md`
- `docs/deployment/ci_cd_pipeline.md`
- `docs/deployment/infisical_factories.md`

Current supported model:

1. `develop` auto-deploys to staging.
2. `main` auto-deploys to production.
3. Manual VPS deploy flows are not the primary path anymore.
