# Deployment Architecture Overview

This document defines the current supported deployment model for Universal Agent.

## Git Branching Model

We use branch-driven automated deployment. Feature work can happen on short-lived branches, but the only deploy branches are `develop` and `main`.

- **Feature branches**: local coding and PR preparation.
- **`develop`**: automated staging deployment target.
- **`main`**: automated production deployment target.

## Environmental Mapping

Each deployed branch maps to a VPS checkout and runtime lane.

| Component | Staging Environment | Production Environment |
|-----------|---------------------|------------------------|
| Git Branch | `develop` | `main` |
| VPS Checkout | `/opt/universal-agent-staging` | `/opt/universal_agent` |
| Fallback Checkout | n/a | `/opt/universal_agent_repo` |
| Service Restart Strategy | `systemctl` or `service` fallback for staging gateway/api units | `systemctl` or `service` fallback for production gateway/api/webui/telegram units |
| Secrets Behavior | Provision `staging-hq`; temporary fallback to `dev` if provisioning fails | No auto-clone from `dev`; production secrets remain curated separately |

## Supported Deployment Rule

1. Push or merge to `develop` to deploy staging automatically.
2. Push or merge to `main` to deploy production automatically.
3. Do not use local-to-VPS file sync as the default deployment path.
4. The canonical deployment runbooks live in `docs/deployment/`.
