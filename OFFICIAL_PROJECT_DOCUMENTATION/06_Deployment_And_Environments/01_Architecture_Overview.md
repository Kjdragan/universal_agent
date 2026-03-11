# Deployment Architecture Overview

## The Challenge
Historically, developing or deploying new features required interrupting the live VPS services, creating downtime, and breaking active sessions.

## The Solution: 3-Tier Environment Isolation

To ensure stability, Universal Agent deploys are separated into three primary contexts governed strictly by Git branches. 

### 1. Local (Development)
**Where it happens:** Your Workstation / Local PC
**Branch:** feature branches (e.g., `feature/new-agent`, `bugfix/crash`)
**Infisical Environment:** `kevins-desktop`, `kevins-tablet`, etc.
**Purpose:** Rapid iteration, breaking things, creating new skills. This environment is inherently unstable and isolated.

### 2. Staging (Testing & CI)
**Where it happens:** The VPS (running `universal-agent-staging.service`)
**Branch:** `develop`
**Infisical Environment:** `staging-hq`, `staging-worker`, etc.
**Purpose:** Once local work is verified, it is PR'd to `develop`. The CI/CD pipeline automatically pulls the code, syncs secrets to a specific Staging Infisical Environment, and gracefully restarts *only* the staging service. You can use this service to test the identical conditions of the VPS without touching production.

### 3. Production (Stable)
**Where it happens:** The VPS (running `universal-agent-prod.service`)
**Branch:** `main`
**Infisical Environment:** `prod-hq`, `prod-worker`, etc.
**Purpose:** The daily driver. Code only arrives here after it survives Staging. A PR from `develop` -> `main` triggers a production deployment with near-zero downtime.

## Guiding Principles

1. **Never develop on `main` or against production secrets.**
2. **One Service = One Infisical Environment.** We do not share a single monolithic `.env` block across multiple running services.
3. **Automated Deployments:** You should never have to manually SSH into the VPS to run `git pull` or `systemctl restart`. The GitHub Actions CI/CD pipeline handles it.
4. **Deterministic Dependencies:** Deployments run `uv sync` to ensure the exact python package versions tested locally are mirrored exactly in Staging and Production.
