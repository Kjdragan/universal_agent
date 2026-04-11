# Deployment Architecture Overview

This document defines the current supported deployment model for Universal Agent.

> [!IMPORTANT]
> ## 🔗 Key Dashboard URLs
>
> | Environment | URL | Notes |
> |---|---|---|
> | **Local Dev** | `http://localhost:3000` | Localheadquarters Next.js server |
> | **Production** | `https://app.clearspringcg.com/dashboard` | Public — no VPN needed |
>
> Production deploys via manual automated push to `main`. `develop` is for integration and review only.

> [!IMPORTANT]
> Release verification is SHA-based. The authoritative proof of what is deployed on VPS is the checkout `HEAD` commit, not the branch name reported by the checkout alone.

## Git Branching Model

We use branch-driven automated deployment with a simplified single-environment pipeline.

- **`feature/latest2`**: local coding and PR preparation.
- **`develop`**: integration branch. PRs from `feature/latest2` land here. Devin automated review runs and CI runs, but nothing deploys.
- **`main`**: automated production deployment target. When `main` moves forward via fast-forward from `develop`, the deploy updates the VPS.

## Environmental Mapping

The `deploy.yml` workflow applies to our single environment.

| Component | Production Environment |
|-----------|------------------------|
| Git Branch | `main` |
| VPS Checkout | `/opt/universal_agent` |
| Fallback Checkout | `/opt/universal_agent_repo` |
| Gateway/API Ports | `8002` / `8001` |
| Web UI Port | `3000` |
| Web UI URL | `https://app.clearspringcg.com` (Public) <br> `https://uaonvps` (Tailnet) |
| API URL | `https://api.clearspringcg.com` (Public) <br> `https://uaonvps:8443` (Tailnet) |
| Service Restart Strategy | Deploy installs repo-managed production systemd units plus the VP worker unit template, runs centralized runtime preflight, performs one clean `.venv` rebuild if preflight fails after the first sync, then restarts gateway/api/webui/telegram plus VP workers |
| Post-Deploy Health | See `ci_cd_pipeline.md` > Post-Deploy Health Verification |
| Secrets Behavior | Bootstrap `.env` for stage `production`; webui `.env.local` rendered from Infisical by deploy |

## Infisical Environment Naming

The runtime contract treats Infisical environments as stage lanes:

- `development`
- `production`

*(Note: `staging` was removed in Phase 3A).*

Machine identity is provided by bootstrap values written on each machine:

- `FACTORY_ROLE`
- `UA_DEPLOYMENT_PROFILE`
- `UA_RUNTIME_STAGE`
- `UA_MACHINE_SLUG`

This lets the same stage environment support:

- VPS headquarters runtime
- desktop local worker runtime
- desktop localhost headquarters development runtime

## Tutorial Runtime Contract

The deployed VPS lane is also the default runtime for the YouTube tutorial pipeline.

- Playlist watching, hook transcript ingestion, tutorial artifact generation, and tutorial repo bootstrap run on the deployed VPS checkout.
- Tutorial repo bootstrap defaults to `UA_TUTORIAL_BOOTSTRAP_TARGET_ROOT=<UA_ARTIFACTS_DIR>/tutorial_repos` on VPS.
- Local workstation tutorial processing is supported only as an explicit development fallback and should not be treated as the normal deployed path.
- VPS tutorial ingest should use loopback-first endpoint ordering, typically `http://127.0.0.1:8002/api/v1/youtube/ingest`.

## Systemd Ownership

The base systemd units for deployed application services are part of the repository and are installed on every deploy from templates under `deployment/systemd/templates/`.

- Production deploy renders the canonical units for `universal-agent-gateway`, `universal-agent-api`, `universal-agent-webui`, `universal-agent-telegram`, and the VP worker template against the active checkout path (`/opt/universal_agent` or fallback `/opt/universal_agent_repo`).
- This prevents host-local systemd drift from silently pinning a service to an old checkout, stale working directory, or missing `EnvironmentFile`.
- The managed Python service units pin `PYDANTIC_DISABLE_PLUGINS=logfire-plugin` so Logfire's optional Pydantic plugin cannot auto-load during startup and turn observability into a hard startup dependency.
- Runtime availability and tracing integrity are now separate concerns by design:
  - package bootstrap keeps services fail-open if Logfire import breaks at runtime
  - deploy preflight still blocks a new release unless the target `.venv` can import real OpenTelemetry + Logfire successfully

## Local Runtime Contract

Kevin's desktop has two supported runtime modes:

- localhost headquarters development:
  - `INFISICAL_ENVIRONMENT=development`
  - `FACTORY_ROLE=HEADQUARTERS`
  - `UA_DEPLOYMENT_PROFILE=local_workstation`

- deployed-stage local worker:
  - `INFISICAL_ENVIRONMENT=production`
  - `FACTORY_ROLE=LOCAL_WORKER`
  - `UA_DEPLOYMENT_PROFILE=local_workstation`

## Supported Deployment Rule

1. Fast-forward `main` to `develop`.
2. The `deploy.yml` workflow fires and VPS updates automatically via Github Actions.
3. Use `/ship` to fast-forward main end-to-end, `/checkpoint` to deploy current `develop`, or `/rollback` to reset `main`.
4. The canonical deployment runbooks live in `docs/deployment/`.

## Repo-Backed Coding Sessions On VPS

Production agent sessions may now be explicitly authorized to edit approved repo roots (for example `/opt/universal_agent`) during coding tasks. This changes **execution authority**, not deployment policy.

Important distinction:

- repo-backed coding sessions let Simone / Cody mutate the checked-out repo on the VPS when the session policy and request metadata explicitly authorize that codebase root
- they do **not** change how releases are promoted
- production still deploys only through `main` push triggering `deploy.yml`

In other words: repo-backed coding enables the agent to do the work on the VPS checkout; the supported path for making that work live remains the existing branch-driven CI/CD pipeline.

## Release Verification Rule

When a production incident appears to suggest "the fix is not deployed":

1. verify the live checkout `HEAD` SHA on the target VPS
2. compare that SHA to the validated `main` SHAs
3. only then decide whether you have a deploy-gap problem or a runtime/browser-state problem

Important nuance:
- a checkout can still report `main` from `git branch --show-current` even after being reset to the exact release commit
- treat the deployed `HEAD` SHA as authoritative and the branch label as secondary context
