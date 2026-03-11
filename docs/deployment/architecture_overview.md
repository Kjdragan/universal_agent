# Deployment Architecture Overview

This document outlines the relationship between our Git branching model, the VPS environments, and the secret management infrastructure.

## Git Branching Model

We follow a structured branching flow to ensure that production remains stable while staging allows for verification of changes.

- **`dev-parallel`**: The active feature/development branch where daily work occurs.
- **`develop`**: The integration branch for testing. Merges into this branch trigger the **Staging** deployment.
- **`main`**: The stable production branch. Merges into this branch trigger the **Production** deployment.

## Environmental Mapping

Each environment is mapped to a specific service and Infisical environment to ensure total isolation.

| Component | Staging Environment | Production Environment |
|-----------|---------------------|------------------------|
| Git Branch | `develop` | `main` |
| VPS Service | `universal-agent-staging-gateway.service` | `universal-agent-prod-gateway.service` |
| VPS Directory | `/opt/universal-agent-staging` | `/opt/universal-agent-prod` |
| Infisical Env | `staging-hq` | `prod-hq` |
| Factory Role | `HEADQUARTERS` | `HEADQUARTERS` |

## Infrastructure Pillars

1. **Isolation**: Code, dependencies, and secrets are physically and logically separated between Staging and Production.
2. **Determinism**: We use `uv lock` to ensure that identical dependency versions are installed across all environments.
3. **Automation**: Secret provisioning is handled by the `infisical_provision_factory_env.py` script, eliminating manual data entry errors.
