# Deployment Architecture Overview

This document defines the current supported deployment model for Universal Agent.

> [!IMPORTANT]
> ## 🔗 Key Dashboard URLs
>
> | Environment | URL | Notes |
> |---|---|---|
> | **Staging** | `https://srv1360701.taildcc090.ts.net:9443/dashboard` | Tailscale required — yellow staging banner visible |
> | **Production** | `https://app.clearspringcg.com/dashboard` | Public — no VPN needed |
>
> Staging deploys from `develop` automatically. Production deploys via manual SHA promotion from `main`.

## Git Branching Model

We use branch-driven automated deployment with a single PR review gate on `develop`.

- **Feature branches**: local coding and PR preparation.
- **PR to `develop`**: Codex review gate.
- **`develop`**: automated staging deployment target.
- **`main`**: automated production deployment target via exact-SHA promotion from validated `develop`.

## Environmental Mapping

Each deployed branch maps to a VPS checkout and runtime lane.

| Component | Staging Environment | Production Environment |
|-----------|---------------------|------------------------|
| Git Branch | `develop` | `main` |
| VPS Checkout | `/opt/universal-agent-staging` | `/opt/universal_agent` |
| Fallback Checkout | n/a | `/opt/universal_agent_repo` |
| Gateway/API Ports | `9002` / `9001` via `UA_GATEWAY_PORT`, `UA_API_PORT`, `UA_GATEWAY_URL=http://127.0.0.1:9002` | `8002` / `8001` |
| Web UI Port | `3001` | `3000` |
| Web UI URL | `https://srv1360701.taildcc090.ts.net:9443` (Tailnet) | `https://app.clearspringcg.com` (Public) <br> `https://srv1360701.taildcc090.ts.net` (Tailnet) |
| API URL | Proxied via Web UI | `https://api.clearspringcg.com` (Public) <br> `https://srv1360701.taildcc090.ts.net:8443` (Tailnet) |
| Service Restart Strategy | `systemctl` or `service` fallback for staging gateway/api units | `systemctl` or `service` fallback for production gateway/api/webui/telegram units |
| Secrets Behavior | Bootstrap into stage env `staging` and validate stage secrets; machine identity is written locally in `.env` | Bootstrap into stage env `production` and validate stage secrets; machine identity is written locally in `.env` |

## Infisical Environment Naming

The runtime contract now treats Infisical environments as stage lanes, not machine lanes:

- `development`
- `staging`
- `production`

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

## Local Runtime Contract

Kevin's desktop has two supported runtime modes:

- localhost headquarters development:
  - `INFISICAL_ENVIRONMENT=development`
  - `FACTORY_ROLE=HEADQUARTERS`
  - `UA_DEPLOYMENT_PROFILE=local_workstation`

- deployed-stage local worker:
  - `INFISICAL_ENVIRONMENT=staging` or `production`
  - `FACTORY_ROLE=LOCAL_WORKER`
  - `UA_DEPLOYMENT_PROFILE=local_workstation`

## Supported Deployment Rule

1. Open a PR to `develop` for Codex review of feature work.
2. Merge to `develop` to deploy staging automatically.
3. Promote the exact validated `develop` SHA to `main` using the promotion workflow.
4. Do not use local-to-VPS file sync as the default deployment path.
5. The canonical deployment runbooks live in `docs/deployment/`.
