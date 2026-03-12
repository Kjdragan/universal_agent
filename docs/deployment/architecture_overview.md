# Deployment Architecture Overview

This document defines the current supported deployment model for Universal Agent.

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
| Service Restart Strategy | `systemctl` or `service` fallback for staging gateway/api units | `systemctl` or `service` fallback for production gateway/api/webui/telegram units |
| Secrets Behavior | Provision `staging-hq`; temporary fallback to `dev` if provisioning fails | No auto-clone from `dev`; production secrets remain curated separately |

## Tutorial Runtime Contract

The deployed VPS lane is also the default runtime for the YouTube tutorial pipeline.

- Playlist watching, hook transcript ingestion, tutorial artifact generation, and tutorial repo bootstrap run on the deployed VPS checkout.
- Tutorial repo bootstrap defaults to `UA_TUTORIAL_BOOTSTRAP_TARGET_ROOT=/opt/universal_agent_data/tutorial_repos` on VPS.
- Local workstation tutorial processing is supported only as an explicit development fallback and should not be treated as the normal deployed path.
- VPS tutorial ingest should use loopback-first endpoint ordering, typically `http://127.0.0.1:8002/api/v1/youtube/ingest`.

## Supported Deployment Rule

1. Open a PR to `develop` for Codex review of feature work.
2. Merge to `develop` to deploy staging automatically.
3. Promote the exact validated `develop` SHA to `main` using the promotion workflow.
4. Do not use local-to-VPS file sync as the default deployment path.
5. The canonical deployment runbooks live in `docs/deployment/`.
