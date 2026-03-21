# Repository Agent Rules

## Problem-Solving Philosophy

When investigating or fixing issues, always solve the **root cause** holistically — never just cure symptoms with band-aids. Before implementing a fix, ask:

1. **Can we expand capabilities** rather than restrict them? (e.g., raise a system limit rather than cap our data to fit under it)
2. **Is there a proper architectural pattern** for this? (e.g., write large data to files instead of stuffing it into env vars)
3. **Are we losing information or functionality** with this approach? If yes, find a better way.

Defensive guards and safety nets are acceptable as a *last resort backstop*, but they must not be the primary fix. The primary fix should eliminate the problem at its source.

## Deployment

This repository has exactly one supported application deployment path:

1. Merge reviewed feature work into `develop` to deploy to staging automatically via GitHub Actions.
2. Promote the validated `develop` SHA to `main` via the manual GitHub Actions promotion workflow to deploy to production.

Canonical deployment docs:

- `docs/deployment/architecture_overview.md`
- `docs/deployment/ci_cd_pipeline.md`
- `docs/deployment/infisical_factories.md`

Do not treat any older manual VPS deployment flow as canonical.

- `scripts/deploy_vps.sh` is legacy and not the primary deployment path.
- `scripts/vpsctl.sh` is a break-glass diagnostics helper, not the normal deployment path.
- `docs/03_Operations/27_Deployment_Runbook_2026-02-12.md` is deprecated.

## Current Deployment Contract

- `develop` deploys to staging on the VPS checkout at `/opt/universal-agent-staging`.
- `main` deploys to production on the VPS checkout at `/opt/universal_agent`, with `/opt/universal_agent_repo` as the safe fallback checkout path if the legacy directory is occupied.
- Pull requests into `develop` are the single Codex review gate.
- Production promotion is a direct, exact-SHA fast-forward from validated `develop` to `main`; there is no second PR review on `main`.
- Tailscale CI access is GitHub Actions -> `TAILSCALE_AUTHKEY` -> tag identity `tag:ci-gha`.
- Production is branch-driven and automated; do not recommend ad hoc `ssh`, `rsync`, or `git pull` as the default deployment method.

## Documentation

All documentation for this project MUST reside exclusively within the `docs/` directory. Creating any other documentation directories (such as `OFFICIAL_PROJECT_DOCUMENTATION/`) is strictly prohibited.

When asked to update or create documentation:

1. **Always Check the Indexes First**: You must consult `docs/README.md` and `docs/Documentation_Status.md` before proceeding.
2. **Update Over Create**: If a document already exists for your topic, update the existing file rather than creating a new one.
3. **Log New Documents**: If you must create a new file, you are required to add a link and description of that new file to both `docs/README.md` and `docs/Documentation_Status.md`.
4. **No Unindexed Files**: No document should exist in `docs/` without being linked from one of the two index files.

## If You Change Deployment Behavior

When editing `.github/workflows/deploy-staging.yml` or `.github/workflows/deploy-prod.yml`, update the canonical docs in `docs/deployment/` in the same change.

## Review guidelines

These guidelines apply when Codex reviews pull requests targeting `develop`.

- Flag any code that logs, stores, or transmits PII or secrets without explicit redaction.
- Verify that every new or modified API route is wrapped by the appropriate authentication/authorization middleware.
- Flag blocking I/O (database calls, HTTP requests) that runs inside an async event loop without `await` or proper executor offloading.
- Verify that background tasks and service loops handle exceptions so they don't silently die.
- Flag Python code that imports secrets or API keys from environment variables directly instead of using the Infisical secret service (our canonical secrets provider — never `.env` files or `os.getenv` for secrets).
- Flag changes that touch `.github/workflows/deploy-staging.yml` or `.github/workflows/deploy-prod.yml` if the corresponding canonical docs in `docs/deployment/` were not updated in the same PR.
- Do not flag formatting-only issues (whitespace, line length) unless they break a linter gate.
- Treat typos in user-facing strings or documentation as P1.
